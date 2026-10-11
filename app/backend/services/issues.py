"""Findings -> issues (tickets). One issue per (site, object, check); repeats update it.

Only ALARM and WARN findings open tickets; INFO findings stay in the register.
Lifecycle: open -> acknowledged -> resolved; resolved/acknowledged can be re-opened. A resolved
issue re-opens automatically when the finding appears on a production day after it was resolved.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from flowval.findings import SEVERITY_ORDER

from ..models import Finding, Issue, IssueComment, Run, now
from ..security import audit

TICKET_SEVERITIES = ("ALARM", "WARN")
TRANSITIONS = {
    "open": {"acknowledged", "resolved"},
    "acknowledged": {"resolved", "open"},
    "resolved": {"open"},
}


def sync_issues(db: Session, run: Run) -> None:
    rows = db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    groups = {}
    for f in rows:
        groups.setdefault((f.object_id, f.check), []).append(f)
    for (oid, check), fs in groups.items():
        fs.sort(key=lambda f: f.date)
        tickets = [f for f in fs if f.severity in TICKET_SEVERITIES]
        issue = db.scalar(select(Issue).where(Issue.site_id == run.site_id, Issue.object_id == oid,
                                              Issue.check == check))
        if issue is None and not tickets:
            continue
        worst = max(fs, key=lambda f: (SEVERITY_ORDER.get(f.severity, 0), f.date))
        last = fs[-1]
        if issue is None:
            issue = Issue(site_id=run.site_id, object_type=last.object_type, object_id=oid, check=check,
                          layer=last.layer, severity=worst.severity, state="open", first_seen=fs[0].date,
                          last_seen=last.date, last_message=last.message, last_action=last.action,
                          last_run_id=run.id)
            db.add(issue)
            db.flush()
        else:
            if issue.state == "resolved" and last.date > issue.last_seen and tickets:
                issue.state = "open"
                db.add(IssueComment(issue_id=issue.id, user="system",
                                    text=f"Re-opened: finding seen again on {last.date} (run {run.id})"))
            issue.severity = worst.severity
            issue.first_seen = min(issue.first_seen, fs[0].date)
            if last.date >= issue.last_seen:
                issue.last_seen, issue.last_message, issue.last_action = last.date, last.message, last.action
            issue.last_run_id = run.id
            issue.updated_at = now()
        for f in fs:
            f.issue_id = issue.id
    db.flush()
    # occurrences = distinct production days on which the finding was ever reported
    counts = db.execute(select(Finding.issue_id, func.count(func.distinct(Finding.date)))
                        .where(Finding.issue_id.isnot(None)).group_by(Finding.issue_id)).all()
    ids = {i for i in (f.issue_id for f in rows) if i}
    for iid, n in counts:
        if iid in ids:
            db.get(Issue, iid).occurrences = int(n)


def update_issue(db: Session, issue: Issue, user: str, state: Optional[str] = None,
                 assignee: Optional[str] = None, due: Optional[str] = None, comment: Optional[str] = None,
                 clear_assignee: bool = False) -> Issue:
    before = dict(state=issue.state, assignee=issue.assignee, due=issue.due)
    if state is not None and state != issue.state:
        if state not in TRANSITIONS.get(issue.state, set()):
            raise HTTPException(409, f"Cannot move an issue from {issue.state} to {state}")
        if state == "resolved" and not (comment or "").strip():
            raise HTTPException(400, "A comment is required to resolve an issue")
        issue.state = state
    if assignee is not None or clear_assignee:
        issue.assignee = (assignee or None) if not clear_assignee else None
    if due is not None:
        issue.due = due or None
    if comment:
        db.add(IssueComment(issue_id=issue.id, user=user, text=comment))
    issue.updated_at = now()
    after = dict(state=issue.state, assignee=issue.assignee, due=issue.due)
    audit(db, user, "issue.update", f"issue:{issue.id} {issue.object_id}/{issue.check}", before,
          {**after, "comment": comment} if comment else after)
    return issue

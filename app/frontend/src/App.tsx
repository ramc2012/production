import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { useApp } from "./context";
import { Account, Login } from "./pages/Login";
import { Admin } from "./pages/Admin";
import { Allocation } from "./pages/Allocation";
import { Dashboard } from "./pages/Dashboard";
import { DataIn } from "./pages/DataIn";
import { Exceptions } from "./pages/Exceptions";
import { MeterDetail, Meters } from "./pages/Meters";
import { Network } from "./pages/Network";
import { Reconciliation } from "./pages/Reconciliation";
import { Runs } from "./pages/Runs";
import { Tanks } from "./pages/Tanks";
import { Tests } from "./pages/Tests";
import { Recommendations, Twin, WhatIf } from "./pages/Twin";
import { WellDetail, Wells } from "./pages/Wells";

export function App() {
  const { user } = useApp();
  if (!user) return <Login />;
  if (user.must_change_password) return <Account forced />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/network" element={<Network />} />
        <Route path="/exceptions" element={<Exceptions />} />
        <Route path="/wells" element={<Wells />} />
        <Route path="/wells/:id" element={<WellDetail />} />
        <Route path="/tests" element={<Tests />} />
        <Route path="/meters" element={<Meters />} />
        <Route path="/meters/:id" element={<MeterDetail />} />
        <Route path="/tanks" element={<Tanks />} />
        <Route path="/separators" element={<Tanks tab="separators" />} />
        <Route path="/reconciliation" element={<Reconciliation />} />
        <Route path="/allocation" element={<Allocation />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/twin" element={<Twin />} />
        <Route path="/twin/whatif" element={<WhatIf />} />
        <Route path="/twin/recommendations" element={<Recommendations />} />
        <Route path="/data" element={<DataIn />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/account" element={<Account />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

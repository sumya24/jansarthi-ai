import { Navigate, Route, Routes } from "react-router-dom";
import LanguageGate from "./pages/LanguageGate";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import ForgotPassword from "./pages/ForgotPassword";
import CitizenHome from "./pages/CitizenHome";
import CitizenDashboard from "./pages/CitizenDashboard";
import CitizenComplaintDetail from "./pages/CitizenComplaintDetail";
import ReportIssue from "./pages/ReportIssue";
import AskSarthi from "./pages/AskSarthi";
import MyArea from "./pages/MyArea";
import WorkerDashboard from "./pages/WorkerDashboard";
import WorkerComplaintDetail from "./pages/WorkerComplaintDetail";
import AdminDashboard from "./pages/AdminDashboard";
import AdminComplaintDetail from "./pages/AdminComplaintDetail";
import AdminAiMonitoring from "./pages/AdminAiMonitoring";
import AdminWorkers from "./pages/AdminWorkers";
import AdminAdmins from "./pages/AdminAdmins";
import AdminWorkerDetail from "./pages/AdminWorkerDetail";
import ProtectedRoute from "./components/ProtectedRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LanguageGate />} />
      <Route path="/welcome" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route
        path="/citizen"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <CitizenHome />
          </ProtectedRoute>
        }
      />
      <Route
        path="/citizen/report"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <ReportIssue />
          </ProtectedRoute>
        }
      />
      <Route
        path="/citizen/complaints"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <CitizenDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/citizen/complaints/:id"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <CitizenComplaintDetail />
          </ProtectedRoute>
        }
      />
      <Route
        path="/citizen/ask"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <AskSarthi />
          </ProtectedRoute>
        }
      />
      <Route
        path="/citizen/area"
        element={
          <ProtectedRoute allowedRoles={["citizen"]}>
            <MyArea />
          </ProtectedRoute>
        }
      />
      <Route
        path="/worker"
        element={
          <ProtectedRoute allowedRoles={["worker"]}>
            <WorkerDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/worker/complaints/:id"
        element={
          <ProtectedRoute allowedRoles={["worker"]}>
            <WorkerComplaintDetail />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/complaints/:id"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminComplaintDetail />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/ai-monitoring"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminAiMonitoring />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/workers"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminWorkers />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/workers/:id"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminWorkerDetail />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/admins"
        element={
          // Any admin can reach this route -- the page itself (and every /admin/admins* backend
          // call it makes) enforces the real super-admin-only restriction, surfacing a clear
          // error banner for a non-super admin rather than needing a second role concept here.
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminAdmins />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

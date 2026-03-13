import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import {
  SignedIn,
  SignedOut,
  RedirectToSignIn,
  useAuth,
} from "@clerk/clerk-react";
import { setTokenGetter } from "./api";
import { Layout } from "./components/Layout";
import { DashboardPage } from "./pages/DashboardPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RepositoryDetailPage } from "./pages/RepositoryDetailPage";
import { ScanDetailPage } from "./pages/ScanDetailPage";
import { ComparisonPage } from "./pages/ComparisonPage";
import "./App.css";

function AuthInit() {
  const { getToken } = useAuth();
  useEffect(() => {
    setTokenGetter(getToken);
  }, [getToken]);
  return null;
}

export default function App() {
  return (
    <>
      <AuthInit />
      <SignedOut>
        <RedirectToSignIn />
      </SignedOut>
      <SignedIn>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/repositories" element={<RepositoriesPage />} />
            <Route path="/repositories/:repoId" element={<RepositoryDetailPage />} />
            <Route path="/scans/:scanId" element={<ScanDetailPage />} />
            <Route path="/repositories/:repoId/compare" element={<ComparisonPage />} />
          </Route>
        </Routes>
      </SignedIn>
    </>
  );
}

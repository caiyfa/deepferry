import { HashRouter, Route, Routes } from "react-router-dom";
import { QueryProvider } from "@/context/QueryContext";
import { Sidebar } from "@/components/Sidebar";
import { StatusBar } from "@/components/StatusBar";
import { QueryPage } from "@/pages/QueryPage";
import { ResultsPage } from "@/pages/ResultsPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { ExecutionPage } from "@/pages/ExecutionPage";

export default function App() {
  return (
    <QueryProvider>
      <HashRouter>
        <div className="df-app">
          <Sidebar />
          <main className="df-main">
            <Routes>
              <Route path="/" element={<QueryPage />} />
              <Route path="/results" element={<ResultsPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/history/:id" element={<ExecutionPage />} />
              <Route path="*" element={<QueryPage />} />
            </Routes>
          </main>
          <StatusBar />
        </div>
      </HashRouter>
    </QueryProvider>
  );
}

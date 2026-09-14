import { useEffect, useState } from "react";
import "./App.css";

const SERVER_URL = import.meta.env.VITE_SERVER_URL || "http://localhost:8000";

function App() {
  const [status, setStatus] = useState("checking...");

  useEffect(() => {
    fetch(`${SERVER_URL}/health`)
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch(() => setStatus("unreachable"));
  }, []);

  return (
    <section id="center">
      <h1>AgentForge</h1>
      <p>Backend status: {status}</p>
    </section>
  );
}

export default App;

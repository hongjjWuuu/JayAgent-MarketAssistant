import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { initializeAuth } from "./api";
import "./styles.css";
import "./runtime.css";

initializeAuth().then(() => ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>));

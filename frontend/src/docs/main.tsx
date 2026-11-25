import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "../i18n";
import "../index.css";
import "../designs/slop2gether.css";
import "./docs.css";
import "../lib/theme";
import { DocsSite } from "./DocsSite";
import { SignIn } from "./SignIn";

// A served doc site (app/plainboard/api/docs_public.py): the server rendered the page into #docs-root for search
// engines and readers without the script, and handed its data in window.__PB_DOCS__.
const state = window.__PB_DOCS__;
const root = document.getElementById("docs-root");
if (state && root) {
  createRoot(root).render(
    <StrictMode>{state.sign_in ? <SignIn name={state.site.name} /> : <DocsSite site={state.site} initial={state.page} initialCustom={state.custom ?? null} />}</StrictMode>,
  );
}

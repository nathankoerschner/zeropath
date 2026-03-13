Build an End-to-End LLM-Powered Python Security Scanner Platform

active
Category:

ai-solution
Role:

All Roles
Problem Statement

AppSec engineers need a centralized platform to scan Python repositories for vulnerabilities, triage findings, and track remediation over time. Your task is to build a working web application that accepts Git repository URLs, uses an LLM to perform security analysis of Python source code, and presents results through an authenticated dashboard with a triage workflow.

Functional Requirements

Authenticated web dashboard with login/signup Submit a Git repo URL and kick off a scan LLM-powered scanner that analyzes Python source files and identifies security vulnerabilities across any vulnerability class the model can reason about Structured findings with severity, vulnerability type, file path, line number, description, and LLM-generated explanation Scan status tracking (queued, running, complete, failed) Triage workflow: mark findings as open, false positive, or resolved with optional notes Scan history per repo with the ability to compare across scans (new, fixed, persisting findings) Finding deduplication and identity across scans REST or GraphQL API with clean separation from the frontend README covering your approach, architecture decisions, tradeoffs, and what you'd build next. Specifically address how you designed your prompts, how you handle LLM output parsing, how you manage token/context window limitations across larger codebases, and how you approached finding stability across scans. README as a Deliverable Your README is as important as your code. It should cover: Architecture overview and key design decisions How you designed your prompts and handle LLM output parsing How you manage token/context window limitations across larger codebases How you approached finding identity and stability across scans What you chose not to build and why What you'd build next given another week Any known limitations or shortcuts you took deliberately We use the README to evaluate product thinking, prioritization, and how well you understand the problem space beyond just getting code to run.

Required Languages

Python (backend/scanner), JavaScript/TypeScript

Technical Contact

raphael@zeropath.com and dean@zeropath.com

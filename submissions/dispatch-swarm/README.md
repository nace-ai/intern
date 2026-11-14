# Dispatch Swarm

Drexathon 2026 submission — Swarm Intelligence track.

## Team
Bharathvaz Reddy Narravula and Ayush Gajbhiye.

## Project repository
https://github.com/Eirch-47/dispatch-swarm

## Overview
Dispatch Swarm is a synthetic emergency-response simulation with
14 agents: six medical units, four fire units and four rescue units.

Agents choose incidents and negotiate competing claims through
Drex decision questions. The dashboard displays assignments,
probabilities and agent activity.

## Evaluation
Four seeded scenarios compare the swarm with an oldest-first
dispatcher that selects the nearest available matching unit.

Metrics include severity served, incidents resolved, losses and
time to first response.

## Review the implementation
The project repository contains:
- server.py — Python backend and Drex integration.
- index.html — simulation, dashboard and benchmark.
- samples/ — synthetic field reports.
- README.md — setup instructions and limitations.

## Scope
The city, emergencies and responder movements are simulated.
NDI document parsing remains unverified.
Benchmark conclusions apply to the tested runs.

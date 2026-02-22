<div align="center">

# 🏃‍♂️ Personal AI OS
### Autonomous Agentic System v2.7.0 (The Coach Dyno Edition)

![Status](https://img.shields.io/badge/Status-Live-success?style=for-the-badge)
![Architecture](https://img.shields.io/badge/Architecture-Modular%20Monolith-orange?style=for-the-badge)
![AI Model](https://img.shields.io/badge/AI-Gemini%202.0%20Flash-blue?style=for-the-badge)
![Memory](https://img.shields.io/badge/Memory-SQLite%20%2B%20ChromaDB-lightgrey?style=for-the-badge)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge)

*A proactive, context-aware AI Agent operating on a lightweight Home Lab (Lenovo T440), evolving from a reactive chatbot to a fully autonomous orchestrator.*

</div>

---

## 📖 1. Project Introduction

**Personal AI OS** is a specialized, multi-tenant capable AI Agent system. [cite_start]Currently incarnated as **Coach Dyno**, its primary mission is to autonomously guide the user towards a **Sub 1:45 Half Marathon** [cite: 22-23].

**The Agentic Core:**
[cite_start]Unlike traditional chatbots that rely on "prompt stuffing", this system is built on the four pillars of an Autonomous Agent[cite: 24]:
1. [cite_start]**Perception:** Real-time ingestion of Strava webhooks, Telegram chats, and Cron-based temporal awareness[cite: 24].
2. [cite_start]**Memory (Dual-System):** Combining structured sports science data (SQLite) with semantic long-term memory (ChromaDB RAG)[cite: 25].
3. [cite_start]**Reasoning:** Leveraging Google's Gemini 2.0 Flash for low-latency, high-IQ analysis and Goal Confidence Score (GCS) forecasting[cite: 26].
4. [cite_start]**Action:** Updating dashboards, interacting on Telegram, and managing physical training loads without human prompting[cite: 27].

**Core Philosophy: "Zero-Heavy Local Processing"**
[cite_start]To operate smoothly on an 8GB RAM machine, heavy LLM reasoning is offloaded to Google APIs, while local resources are strictly reserved for the Vector Database (ChromaDB) and lightweight state management (FastAPI)[cite: 28].

---

## 🏗️ 2. System Architecture

[cite_start]The system utilizes a decoupled Modular Monolith infrastructure[cite: 29].

```mermaid
graph TD
    %% External Inputs
    User(("🏃 Runner")) -->|"Telegram Chat"| [cite_start]Telegram["Telegram Webhook"] [cite: 30-31]
    StravaCloud["Strava Cloud"] -->|"Activity Webhook"| [cite_start]Nginx [cite: 31]

    %% Infra
    subgraph "INFRASTRUCTURE (runner-net)"
        [cite_start]Nginx["Nginx Proxy Manager"] [cite: 31]
        [cite_start]SSL["Let's Encrypt"] [cite: 31]
    end

    %% Application
    subgraph "MODULAR MONOLITH (FastAPI)"
        [cite_start]Gateway["main.py Gateway"] [cite: 31]
        
        subgraph "API Layer"
            [cite_start]HookRouter["Webhooks"] [cite: 31]
            [cite_start]AdminRouter["Admin/User UI"] [cite: 32]
        end
        
        subgraph "Dual-Memory System"
            [cite_start]DB[("SQLite (TRIMP/ACWR)")] [cite: 32]
            [cite_start]VectorDB[("ChromaDB (RAG)")] [cite: 32]
        end
        
        subgraph "Domain Logic (Agents)"
            [cite_start]Coach["Coach Agent"] [cite: 32]
            [cite_start]StravaAPI["Strava Client"] [cite: 33]
        end
        
        subgraph "Background Workers"
            [cite_start]Cron["APScheduler"] [cite: 33]
        end
    end

    %% External LLM
    [cite_start]Gemini["Google Gemini 2.0 API"] [cite: 33]

    %% Connections
    [cite_start]Telegram --> Nginx [cite: 33]
    Nginx -->|"Reverse Proxy :8000"| [cite_start]Gateway [cite: 33-34]
    [cite_start]Gateway --> HookRouter [cite: 34]
    [cite_start]Gateway --> AdminRouter [cite: 34]
    
    [cite_start]HookRouter --> Coach [cite: 34]
    Cron -->|"Trigger Harvest/Briefing"| [cite_start]Coach [cite: 34-35]
    Coach <-->|"Math/Metrics"| [cite_start]DB [cite: 35]
    Coach <-->|"Semantic Context"| [cite_start]VectorDB [cite: 35]
    Coach <-->|"Fetch CSV/Stats"| [cite_start]StravaAPI [cite: 35-36]
    Coach <-->|"Reasoning & Tool Use"| [cite_start]Gemini [cite: 36]


```

---

## 🧠 3. The Dual-Memory Engine

Personal AI OS separates data into two distinct tiers to optimize context window and reasoning speed:

* **Tier 1: Relational (SQLite - `data/os_core.db`)**
Tracks high-precision mathematical states: Acute/Chronic Workload Ratios (ACWR), TRIMP loads, Goal Confidence Scores (GCS), and Stateful Training Plans.


* **Tier 2: Vector Semantic (ChromaDB - `data/chroma_db`)**
Stores unstructured historical knowledge. When the user asks a question, the Agent queries this RAG database to recall specific form corrections, past injuries, and historical run contexts.



---

## 💻 4. Deployment & Operation

### Prerequisites

1. Docker and Docker Compose installed.


2. A `.env` file at the root containing API Keys (Gemini, Telegram, Strava, SMTP) and `CHROMADB_CACHE_DIR` routing.



### Quick Start

To spin up the entire OS (Application + Nginx Proxy):

```bash
docker-compose up -d --build

```

### Dashboards

* 
**Admin Control Center:** `https://<your-domain>/admin` (System Prompts, Log Tracking, Model Switching).


* 
**User Performance Dashboard:** `https://<your-domain>/dashboard` (Visualizing Garmin-style ACWR, Banister TRIMP trends, and GCS).



---

## 🗺️ 5. The Agentic Evolution Roadmap 2.0

### ✅ Phase 1 & 2: The Sensing Foundation (Completed)

* [x] Hybrid Strava Sync (Webhooks + Auto-Harvest with 5s Downsampling).


* [x] Dual-Memory architecture (SQLite + ChromaDB RAG).


* [x] Automated Goal Confidence Score (GCS) extraction and visualization.


* [x] Smart Retry mechanisms for 429 API rate limits.



### ✅ Phase 3: The Tool-Using Expert (Completed)

* [x] **Automatic Function Calling (AFC):** Transition from "prompt stuffing" to dynamic tool usage. Equipped the Agent with python tools (`check_training_status`, `get_recent_workouts`, `search_long_term_memory`, `set_workout_plan`) .


* [x] **Dynamic Prompting:** The Agent decides *when* and *what* data to fetch based on user intent, drastically reducing token usage.


* [ ] **Self-Correction:** The Agent learns to retry with different parameters if a tool returns an error.



### 🚀 Phase 4: Proactive Autonomy (Current Focus)

* [x] **Proactive Interventions:** Background workers monitor ACWR and Chats. The Agent autonomously initiates a Telegram morning standup and modifies the daily training plan to enforce rest if injury risk spikes.


* [ ] **Self-Reflection Loop:** A weekly chron-job where the Agent evaluates its past advice against actual Strava outcomes, adjusting its own configuration and training philosophy dynamically.



### 🔮 Phase 5: The Multi-Agent Ecosystem (Late 2026)

* [ ] **Supervisor Orchestrator:** Convert `main.py` into a Router Agent that delegates tasks.


* [ ] **Finance Agent:** Add personal budget tracking and running gear depreciation analysis.


* [ ] **News Agent (Trafilatura):** Implement RSS-based, zero-heavy crawling to ingest sports science articles directly into the RAG memory.



---

<div align="center">
<sub>Designed and built for Personal Home Lab Operations.</sub>
</div>
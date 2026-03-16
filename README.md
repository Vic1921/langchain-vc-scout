# LangChain VC Scout

A small LangChain experiment to test a simple VC-oriented workflow.

## What it does
- scrapes visible headlines from a webpage
- summarizes the most relevant developments
- converts them into a structured VC-style note

## Stack
- Python
- LangChain
- LangGraph memory/checkpointer
- Claude via Anthropic

## Why I built it
I wanted to better understand how agent workflows behave in practice and how easy it is to turn raw web information into something closer to an investment-oriented research note.

## Setup

1. Clone the repo
2. Create a virtual environment: `python -m venv .venv`
3. Activate it: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Mac/Linux)
4. Install dependencies: `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` and fill in your API keys
6. Run: `python src/main.py`
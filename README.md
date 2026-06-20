# Hello Agent

A small Python project for learning AI agent architecture from first principles.

The current version demonstrates the simplest agent pattern:

1. Receive a request
2. Make a decision
3. Produce a response
4. Save the result to disk

Run it with:

```bash
PYTHONPATH=src python main.py
```

## Structure

```text
.
├── main.py                  # Entry point: creates and runs the agent
├── project_context.md       # Learning goals and project constraints
├── prompt.txt               # Scratch space for future prompt experiments
├── src/
│   └── hello_agent/
│       ├── agent.py         # Agent loop: request, decision, response, save
│       ├── storage.py       # Disk persistence helpers
│       └── __init__.py
├── data/
│   ├── outputs/             # Agent outputs are written here
│   └── memory/              # Reserved for future memory exercises
└── exercises/
    └── README.md            # Suggested learning exercises
```

## Design Notes

This project avoids agent frameworks on purpose. The aim is to see the core
moving parts directly before adding model APIs, tools, memory, or orchestration.


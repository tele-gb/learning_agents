# Exercises

## 1. Minimal Think-Then-Save Agent

Modify `src/hello_agent/agent.py` so the decision step can handle two or three
different kinds of requests.

Focus on the core shape:

```text
request -> decision -> response -> saved output
```

## 2. Add One Tool

Create a tool function that the agent can choose to call, such as:

- `count_words(text)`
- `get_current_time()`
- `save_note(text)`

The point is to learn that tools are ordinary functions exposed to the agent.

## 3. Add Simple Memory

Store a small amount of information in `data/memory/memory.json` and read it
back on the next run.

The point is to learn that memory is external state, not magic.


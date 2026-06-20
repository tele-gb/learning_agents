from hello_agent.agent import SimpleAgent


def main() -> None:
    request = "Write a short note about what an AI agent is."

    agent = SimpleAgent()
    result = agent.run(request)

    print(result)


if __name__ == "__main__":
    main()

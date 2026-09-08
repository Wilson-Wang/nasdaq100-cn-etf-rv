from etf_dataset.research_validation import validate_research_outputs


def main() -> None:
    errors = validate_research_outputs("data")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("research output validation passed")


if __name__ == "__main__":
    main()

def calculate_average(numbers):
    if not numbers:
        raise ValueError("Input list cannot be empty.")
    total = sum(numbers)
    return total / len(numbers)


scores = [80, 90, 100]
print("Average:", calculate_average(scores))

print("Empty average:", calculate_average([]))

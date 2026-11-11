def add_points(score, points):
    return sum([score, points])

score = 0
for amount in (10, 5):
    score = add_points(score, amount)
print(score)

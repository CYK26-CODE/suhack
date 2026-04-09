#!/usr/bin/env bash
# Creates a minimal Python repo at /tmp/test-repo for E2E tests
set -e
REPO=/tmp/test-repo
rm -rf "$REPO"
mkdir -p "$REPO/src" "$REPO/tests"
cd "$REPO"
git init
git config user.email "test@example.com"
git config user.name "Test"

# A simple module with measurable complexity
cat > src/utils.py << 'EOF'
def process(items, threshold):
    result = []
    for item in items:
        if item > threshold:
            if item % 2 == 0:
                result.append(item * 2)
            else:
                result.append(item)
    return result

def add(a, b):
    return a + b
EOF

cat > src/__init__.py << 'EOF'
EOF

# A passing test
cat > tests/test_utils.py << 'EOF'
from src.utils import add, process

def test_add():
    assert add(1, 2) == 3

def test_process_empty():
    assert process([], 5) == []

def test_process_filters():
    assert 3 not in process([1, 2, 3, 4, 5], 3)
EOF

git add .
git commit -m "initial commit"

# A second commit to create churn
sed -i 's/return result/return sorted(result)/' src/utils.py
git add .
git commit -m "sort results"

echo "Test repo seeded at $REPO"

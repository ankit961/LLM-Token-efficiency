def process(req):
    validate(req)          # scoped hard, dist 1
    run_db(req)            # scoped hard, dist 1 -> DOMINANT branch (calls q1..q5)
    charge(req)            # inferred soft (charge unique in gateway.py)
    save(req)              # ambiguous (save in a.py AND b.py) -> hint, no dep
    return req


def validate(req):
    return req is not None


def run_db(req):
    q1(req); q2(req); q3(req); q4(req); q5(req)
    return req


def q1(req): return 1
def q2(req): return 2
def q3(req): return 3
def q4(req): return 4
def q5(req): return 5

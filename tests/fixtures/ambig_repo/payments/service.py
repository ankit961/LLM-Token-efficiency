def process(x):
    log_event(x)        # unique short name across repo -> inferred (SOFT)
    return save(x)      # two candidates -> ambiguous (NO dependency)

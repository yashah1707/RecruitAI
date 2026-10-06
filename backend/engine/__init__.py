"""Stages 3 and 4: the Assessor and the Decision.

Deterministic Python. No model is called anywhere in this package. Every
threshold comes from the rule tables in the database, and every result
carries the clause and gazette page it rests on.

    facts.py       what is known about one application, with honest bounds
    experience.py  years of service from dated posts, and the cl. 3.11 adjustment
    scores.py      Appendix II: the Research Score (Table 2) and the short-listing score (Table 3A)
    rules.py       the rule set that applies to a post, read from the database
    decision.py    the checks for one rank, and the walk down the ranks

A fact is often not known exactly: a post dated "2014 to 2019" is between
four and six years. Such a quantity is carried as a lower and an upper bound.
A requirement then has three possible answers, not two: met, not met, or
cannot be told from what is known. The engine never turns the third into
either of the others; it hands the application to a person, naming what is
missing.
"""

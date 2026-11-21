"""Models that rank alerts by how likely they are to matter, with the governance a regulator expects of them.

    dataset     labelled examples: alerts whose cases closed, plus outcomes a client loads, each with the customer's
                features as they stood when the alert arrived (no hindsight)
    train       scikit-learn, a time-ordered split, and metrics that speak to the work: how many alerts could wait
                while still reaching 95% or 99% of the ones that mattered
    registry    versions, four-eyes approval, champion and challenger, retirement
    scoring     the champion's probability and its top reasons on every new alert; the challenger scored alongside
    monitoring  drift between what a model learnt from and what it now sees (population stability index)

A model only ever ranks and explains: it never closes an alert. What the triage agent may do with a score is the
client's agent policy, as for any other signal.
"""
NAME = "alert_priority"
TRAIN = "tune_detection"
APPROVE = "approve_tuning"

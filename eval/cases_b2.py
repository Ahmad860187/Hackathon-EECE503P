"""Batch 2 of the DEV-ONLY evaluation cases (b2_* and pv_*). Registered by build_cases.py.

Same contract as the cases in build_cases.py: case.json (3 strings), excerpt.txt (<= 300 words, paraphrase),
reference.json (independently computed values, must-mention facts, common errors, 5-question quiz).
Reference values below are computed with plain formulas / small deterministic simulations (stdlib only).

Batch 2 deliberately varies the INPUT FORMAT of source_url (arXiv abs / pdf / html / bare id, DOI URL,
non-arXiv PDF or publisher URL), the focus style (one sentence / long and detailed / explicit checks)
and the audience wording. See eval/BENCHMARKS.md, "Batch 2".
"""
from __future__ import annotations

import math


# ----------------------------------------------------------------------------------------------
# Reference computations
# ----------------------------------------------------------------------------------------------

def bayes_ppv(prev, sens, fpr, n=1000):
    tp = sens * prev
    fp = fpr * (1 - prev)
    tn = (1 - fpr) * (1 - prev)
    fn = (1 - sens) * prev
    return {"P_positive": tp + fp, "PPV": tp / (tp + fp), "NPV": tn / (tn + fn),
            f"true_positives_per_{n}": tp * n, f"false_positives_per_{n}": fp * n}


def dijkstra(graph, src):
    """graph: {u: {v: w}} (undirected stored both ways). Returns dist, prev and settle order (ties by name)."""
    dist = {v: math.inf for v in graph}
    prev = {v: None for v in graph}
    dist[src] = 0
    done, order = set(), []
    while len(done) < len(graph):
        u = min((v for v in graph if v not in done), key=lambda v: (dist[v], v))
        if dist[u] == math.inf:
            break
        done.add(u)
        order.append(u)
        for v, w in graph[u].items():
            if dist[u] + w < dist[v]:
                dist[v], prev[v] = dist[u] + w, u
    return {"dist": dist, "prev": prev, "settle_order": order}


def path_to(prev, t):
    p = []
    while t is not None:
        p.append(t)
        t = prev[t]
    return p[::-1]


def kalman_1d(zs, x0, P0, R, Q=0.0, a=1.0):
    x, P = x0, P0
    out = []
    for z in zs:
        xp, Pp = a * x, a * a * P + Q            # predict
        K = Pp / (Pp + R)                         # gain
        x = xp + K * (z - xp)                     # update
        P = (1 - K) * Pp
        out.append({"z": z, "x_pred": xp, "P_pred": Pp, "K": K, "x": x, "P": P})
    return out


def dropout_layer(h, w, p_keep):
    full = sum(a * b for a, b in zip(h, w))
    n = len(h)
    # exact expectation by enumerating all 2^n masks
    exp_sum = 0.0
    for mask in range(2 ** n):
        prob, s = 1.0, 0.0
        for i in range(n):
            kept = (mask >> i) & 1
            prob *= p_keep if kept else (1 - p_keep)
            if kept:
                s += h[i] * w[i]
        exp_sum += prob * s
    var = p_keep * (1 - p_keep) * sum((a * b) ** 2 for a, b in zip(h, w))
    return {"full_net_input": full, "expected_train_input": exp_sum, "test_input_weights_scaled_by_p": p_keep * full,
            "inverted_dropout_expected_input": exp_sum / p_keep, "train_input_variance": var,
            "num_thinned_networks": 2 ** n}


def batchnorm(x, gamma=1.0, beta=0.0, eps=1e-5):
    m = len(x)
    mu = sum(x) / m
    var = sum((v - mu) ** 2 for v in x) / m          # biased (1/m) mini-batch variance, Algorithm 1
    xh = [(v - mu) / math.sqrt(var + eps) for v in x]
    y = [gamma * v + beta for v in xh]
    ym = sum(y) / m
    return {"mu_B": mu, "var_B": var, "x_hat": xh, "y": y, "mean_y": ym,
            "var_y": sum((v - ym) ** 2 for v in y) / m, "unbiased_var_for_inference": var * m / (m - 1)}


def agm(a, b):
    for _ in range(60):
        a, b = (a + b) / 2, math.sqrt(a * b)
    return a


def pendulum(theta0_deg, L=1.0, g=9.81):
    th = math.radians(theta0_deg)
    T0 = 2 * math.pi * math.sqrt(L / g)
    T = T0 / agm(1.0, math.cos(th / 2))               # exact: T = 4 sqrt(L/g) K(sin(theta0/2))
    T2 = T0 * (1 + th ** 2 / 16)
    T4 = T0 * (1 + th ** 2 / 16 + 11 * th ** 4 / 3072)
    return {"T_small_angle_s": T0, "T_exact_s": T, "T_ratio_exact_over_small": T / T0,
            "small_angle_error_pct": 100 * (T - T0) / T, "T_series_2nd_order_s": T2, "T_series_4th_order_s": T4}


def carnot(Th, Tc, Qh=1000.0):
    eta = 1 - Tc / Th
    return {"efficiency": eta, "W_J": eta * Qh, "Qc_J": Qh - eta * Qh, "Qh_over_Th": Qh / Th,
            "Qc_over_Tc": (Qh - eta * Qh) / Tc if Tc > 0 else None, "COP_refrigerator": Tc / (Th - Tc) if Th > Tc else None}


R_GAS = 8.314


def arrhenius_k(A, Ea, T):
    return A * math.exp(-Ea / (R_GAS * T))


def nash_2x2(Ar, Ac):
    """Ar, Ac: 2x2 payoffs for row and column player; rows T/B, cols L/R. Returns pure NE and interior mixed NE."""
    names = [("T", "L"), ("T", "R"), ("B", "L"), ("B", "R")]
    pure = []
    for i in range(2):
        for j in range(2):
            if Ar[i][j] >= Ar[1 - i][j] and Ac[i][j] >= Ac[i][1 - j]:
                pure.append(names[2 * i + j][0] + names[2 * i + j][1])
    mixed = None
    dc = (Ac[0][0] - Ac[0][1]) - (Ac[1][0] - Ac[1][1])
    dr = (Ar[0][0] - Ar[1][0]) - (Ar[0][1] - Ar[1][1])
    if dc != 0 and dr != 0:
        p = (Ac[1][1] - Ac[1][0]) / dc        # P(row plays T) making column indifferent
        q = (Ar[1][1] - Ar[0][1]) / dr        # P(col plays L) making row indifferent
        if 0 < p < 1 and 0 < q < 1:
            ur = q * Ar[0][0] + (1 - q) * Ar[0][1]
            uc = p * Ac[0][0] + (1 - p) * Ac[1][0]
            mixed = {"p_row_T": p, "q_col_L": q, "row_payoff": ur, "col_payoff": uc}
    return {"pure_equilibria": pure, "mixed_equilibrium": mixed}


def pagerank(links, d=0.85, iters=200):
    """links: {page: [outlinks]} with no dangling pages. Normalised form PR = (1-d)/N + d sum PR/C."""
    pages = sorted(links)
    N = len(pages)
    pr = {p: 1 / N for p in pages}
    for _ in range(iters):
        new = {p: (1 - d) / N for p in pages}
        for u in pages:
            for v in links[u]:
                new[v] += d * pr[u] / len(links[u])
        pr = new
    return {"PR_normalised_sum1": pr, "PR_brin_page_form_sumN": {p: N * v for p, v in pr.items()},
            "sum_normalised": sum(pr.values())}


def schedule(bursts, policy, quantum=None):
    """All jobs arrive at t=0 in the listed order. Returns waiting, turnaround and response times."""
    n = len(bursts)
    rem = list(bursts)
    finish, first = [None] * n, [None] * n
    t = 0
    if policy in ("FCFS", "SJF"):
        order = list(range(n)) if policy == "FCFS" else sorted(range(n), key=lambda i: (bursts[i], i))
        for i in order:
            first[i] = t
            t += bursts[i]
            finish[i] = t
        gantt = order
    else:  # RR
        queue, gantt = list(range(n)), []
        while queue:
            i = queue.pop(0)
            if first[i] is None:
                first[i] = t
            run = min(quantum, rem[i])
            t += run
            rem[i] -= run
            gantt.append(i)
            if rem[i] > 0:
                queue.append(i)
            else:
                finish[i] = t
    wait = [finish[i] - bursts[i] for i in range(n)]
    return {"waiting": wait, "avg_waiting": sum(wait) / n, "turnaround": finish, "avg_turnaround": sum(finish) / n,
            "response": first, "avg_response": sum(first) / n}


def page_faults(refs, frames, policy):
    mem, faults, hist = [], 0, []
    for k, x in enumerate(refs):
        hit = x in mem
        if hit:
            if policy == "LRU":
                mem.remove(x)
                mem.append(x)
        else:
            faults += 1
            if len(mem) == frames:
                if policy in ("FIFO", "LRU"):
                    mem.pop(0)
                else:  # OPT: evict the page used farthest in the future
                    fut = refs[k + 1:]
                    victim = max(mem, key=lambda p: fut.index(p) if p in fut else 10 ** 9)
                    mem.remove(victim)
            mem.append(x)
        hist.append("H" if hit else "F")
    return faults, "".join(hist)


def cuckoo_insert_all(keys, r=5, max_loop=10):
    h1 = lambda k: k % r
    h2 = lambda k: (k // r) % r
    T = [[None] * r, [None] * r]
    log = []
    for key in keys:
        x, side, chain = key, 0, []
        placed = False
        for _ in range(max_loop):
            pos = h1(x) if side == 0 else h2(x)
            chain.append(f"T{side + 1}[{pos}]<-{x}")
            x, T[side][pos] = T[side][pos], x
            if x is None:
                placed = True
                break
            side = 1 - side
        log.append({"key": key, "chain": chain, "evictions": len(chain) - 1, "placed": placed,
                    "left_over_key": None if placed else x})
        if not placed:
            break
    return {"T1": T[0], "T2": T[1], "log": log}


def lorenz_rhs(s, sigma=10.0, rho=28.0, beta=8 / 3):
    x, y, z = s
    return (sigma * (y - x), x * (rho - z) - y, x * y - beta * z)


def rk4(s, dt, **kw):
    k1 = lorenz_rhs(s, **kw)
    k2 = lorenz_rhs(tuple(a + dt / 2 * b for a, b in zip(s, k1)), **kw)
    k3 = lorenz_rhs(tuple(a + dt / 2 * b for a, b in zip(s, k2)), **kw)
    k4 = lorenz_rhs(tuple(a + dt * b for a, b in zip(s, k3)), **kw)
    return tuple(a + dt / 6 * (p + 2 * q + 2 * r + w) for a, p, q, r, w in zip(s, k1, k2, k3, k4))


def lorenz_run(s0, T=40.0, dt=0.001, delta=None, **kw):
    s = s0
    s2 = (s0[0] + delta, s0[1], s0[2]) if delta else None
    t_sep = None
    n = int(round(T / dt))
    for k in range(n):
        s = rk4(s, dt, **kw)
        if s2:
            s2 = rk4(s2, dt, **kw)
            if t_sep is None and math.dist(s, s2) > 1.0:
                t_sep = (k + 1) * dt
    return s, t_sep


# ----------------------------------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------------------------------

def register(case, q):
    """Add the batch-2 cases through build_cases.case / build_cases.q."""

    # b2_bayes_base_rate ------------------------------------------------------------------------
    case(
        "b2_bayes_base_rate",
        "https://doi.org/10.1056/NEJM197811022991808",
        "Explain why a positive result on an accurate test for a rare disease usually still means the person is "
        "probably healthy.",
        "Pre-med biology student who has had one semester of statistics.",
        """
Source: W. Casscells, A. Schoenberger and T. B. Graboys, "Interpretation by Physicians of Clinical
Laboratory Results", New England Journal of Medicine 299 (1978) 999-1001. Paraphrased; the publisher
page returns 403 to scripts, so the study details were checked against secondary descriptions.

The authors put one question to staff and students at Harvard Medical School hospitals: if a test to
detect a disease whose prevalence is 1/1000 has a false-positive rate of 5 percent, what is the chance
that a person found to have a positive result actually has the disease, assuming nothing is known
about the person's symptoms or signs? Only a small minority (about 11 of 60) answered close to the
correct value of roughly 2 percent; the most common answer was 95 percent.

The correct reasoning is Bayes' theorem. Assuming the test detects every true case (sensitivity 1),
out of 1000 people about 1 has the disease and tests positive, while about 5 percent of the 999
healthy people (about 50) also test positive. So only about 1 of 51 positives is a true case:

    P(disease | +) = P(+ | disease) P(disease) / [P(+ | disease) P(disease) + P(+ | healthy) P(healthy)]

The error, now called base-rate neglect or the base-rate fallacy, is to confuse P(+ | disease), or
1 minus the false-positive rate, with P(disease | +), ignoring how rare the disease is. The authors
argued that clinicians need better training in interpreting test results.
""",
        {"title": "Interpretation by Physicians of Clinical Laboratory Results", "authors": "W. Casscells, "
         "A. Schoenberger, T. B. Graboys", "year": 1978, "venue": "N. Engl. J. Med. 299(18):999-1001",
         "url": "https://doi.org/10.1056/NEJM197811022991808", "section": "the single survey question and its answer",
         "verified": "DOI returns 403 to scripts (2026-10-03); question and the 11-of-60 / 95% figures taken from "
                     "standard secondary accounts of the study."},
        ["PPV = se*p / (se*p + fpr*(1-p))", "P(+) = se*p + fpr*(1-p)", "NPV = (1-fpr)(1-p) / ((1-fpr)(1-p) + (1-se) p)",
         "natural frequencies: TP = se*p*N, FP = fpr*(1-p)*N"],
        [
            {"label": "Casscells question", "params": {"prevalence": 0.001, "sensitivity": 1.0, "false_positive_rate": 0.05},
             "expected": bayes_ppv(0.001, 1.0, 0.05), "tol": 1e-4, "note": "PPV ~ 1.96%, not 95%"},
            {"label": "Eddy-style mammography numbers", "params": {"prevalence": 0.01, "sensitivity": 0.8,
             "false_positive_rate": 0.096}, "expected": bayes_ppv(0.01, 0.8, 0.096), "tol": 1e-4},
            {"label": "common disease", "params": {"prevalence": 0.5, "sensitivity": 0.95, "false_positive_rate": 0.05},
             "expected": bayes_ppv(0.5, 0.95, 0.05), "tol": 1e-4, "note": "PPV = 0.95 only when the base rate is 50%"},
            {"label": "second positive test (posterior becomes new prior)", "params": {"prevalence": bayes_ppv(0.001, 1.0, 0.05)["PPV"],
             "sensitivity": 1.0, "false_positive_rate": 0.05},
             "expected": bayes_ppv(bayes_ppv(0.001, 1.0, 0.05)["PPV"], 1.0, 0.05), "tol": 1e-3,
             "note": "assumes the two tests' errors are independent"},
        ],
        [
            "Bayes' theorem: P(D|+) = P(+|D) P(D) / P(+)",
            "With prevalence 1/1000 and 5% false positives, P(disease | positive) is only about 2%",
            "The base-rate fallacy confuses P(+|disease) with P(disease|+) and ignores prevalence",
            "Most false positives come from the large healthy group, even with a small false-positive rate",
            "In the 1978 survey most respondents answered 95%; only a minority (about 11 of 60) gave ~2%",
            "Natural frequencies (e.g. 1 true positive vs ~50 false positives per 1000) make the result intuitive",
        ],
        [
            "Reporting 95% (1 - false-positive rate) as the probability of disease",
            "Dividing true positives by all people instead of all positives",
            "Forgetting the (1 - prevalence) factor on the false-positive term",
            "Treating a second positive test as independent when errors are correlated, without stating the assumption",
            "Probabilities that do not sum to 1 across the four test/disease cells",
        ],
        [
            q("With prevalence 1/1000, sensitivity 100% and 5% false positives, P(disease | positive) is about:",
              ["95%", "50%", "2%", "0.1%"], "C", 1),
            q("What is the base-rate fallacy?", ["Ignoring the prior prevalence when interpreting a test result",
              "Using too large a sample", "Confusing sensitivity with specificity in a lab manual",
              "Rounding probabilities"], "A", 2),
            q("Why are most positive results false in this example?", ["The test is broken",
              "The healthy group is so large that 5% of it outnumbers the true cases", "Sensitivity is low",
              "The disease is contagious"], "B", 3),
            q("Which formula is Bayes' theorem for this problem?", ["P(D|+) = P(+|D)", "P(D|+) = P(+|D) P(D) / P(+)",
              "P(D|+) = 1 - fpr", "P(D|+) = P(D) + P(+)"], "B", 0),
            q("What was the most common answer in the 1978 physician survey?", ["2%", "50%", "95%", "0.1%"], "C", 4),
        ],
        invariants=["0 <= PPV <= 1", "PPV increases with prevalence", "TP + FP + TN + FN == N"],
        benchmark={"name": "classic paper (no benchmark dataset)", "license": "n/a (paraphrase only)",
                   "input_format": "DOI URL", "focus_style": "one sentence"},
    )

    # b2_dijkstra ----------------------------------------------------------------------------------
    edges = [("A", "B", 4), ("A", "C", 2), ("B", "C", 1), ("B", "D", 5), ("C", "D", 8), ("C", "E", 10),
             ("D", "E", 2), ("D", "F", 6), ("E", "F", 2)]
    G = {v: {} for v in "ABCDEF"}
    for u, v, w in edges:
        G[u][v] = w
        G[v][u] = w
    dA, dD = dijkstra(G, "A"), dijkstra(G, "D")
    G2 = {u: dict(nb) for u, nb in G.items()}
    G2["B"]["D"] = G2["D"]["B"] = 1
    dA2 = dijkstra(G2, "A")
    case(
        "b2_dijkstra",
        "https://doi.org/10.1007/BF01386390",
        "Dijkstra (1959), A Note on Two Problems in Connexion with Graphs, Problem 2 (shortest path between two "
        "nodes). Build a step-by-step explorer on a small weighted graph of about six nodes where the learner "
        "picks the start node, edits edge weights and steps through the algorithm one node at a time. "
        "Checks: (1) the tentative-distance table updates only when a shorter path is found; (2) nodes are "
        "settled in non-decreasing order of distance and a settled distance never changes; (3) the final "
        "path can be traced back through predecessors and its weights sum to the reported distance; "
        "(4) explain why negative edge weights break the method.",
        "Second-year computer science student who knows breadth-first search and priority queues.",
        """
Benchmark provenance: TheoremExplainBench theorem_128 "Dijkstra's algorithm" (comp_sci / Graph Theory,
Medium), MIT licence: the algorithm keeps a priority queue of vertices ordered by distance from the
start and repeatedly selects the next shortest path to an unconnected part of the graph. Primary
source: E. W. Dijkstra, "A note on two problems in connexion with graphs", Numerische Mathematik 1
(1959) 269-271. Paraphrased.

Dijkstra considers n nodes connected by branches of given positive length and solves two problems:
(1) the tree of minimum total length connecting all nodes, and (2) the path of minimum total length
between two given nodes P and Q. For problem 2 the nodes are split into three sets: A, nodes whose
minimal path from P is known; B, nodes reachable from A by one branch, each with a tentative
distance; and C, the remaining nodes. Initially A holds only P. Repeatedly: for the node just added
to A, consider every branch to a node R outside A. If R is in C it moves to B with tentative distance
via this branch; if R is already in B, its tentative distance is replaced only if the new path is
shorter. Then the node of B with the smallest tentative distance is moved to A; its distance is now
final. The process stops when Q is moved to A. Because branch lengths are non-negative, no later
path can improve on the smallest tentative distance, which is why it can be fixed. Dijkstra notes the
method needs only the data for the branches considered and is efficient compared with methods that
enumerate paths.
""",
        {"title": "A note on two problems in connexion with graphs", "authors": "E. W. Dijkstra", "year": 1959,
         "venue": "Numerische Mathematik 1:269-271", "url": "https://doi.org/10.1007/BF01386390",
         "section": "Problem 2", "verified": "DOI resolves (200) 2026-10-03; method paraphrased from the paper's "
                                             "description as widely reproduced; TEB description fetched via HF API."},
        ["dist[src] = 0, others = inf", "relax: if dist[u] + w(u,v) < dist[v] then dist[v] = dist[u] + w, prev[v] = u",
         "settle the unsettled node with the smallest tentative distance", "path = follow prev from target"],
        [
            {"label": "start A", "params": {"edges": edges, "source": "A"},
             "expected": {"dist": dA["dist"], "settle_order": dA["settle_order"], "path_A_to_F": path_to(dA["prev"], "F")},
             "tol": 0},
            {"label": "start D", "params": {"edges": edges, "source": "D"},
             "expected": {"dist": dD["dist"], "settle_order": dD["settle_order"], "path_D_to_A": path_to(dD["prev"], "A")},
             "tol": 0},
            {"label": "edit B-D weight to 1", "params": {"edges": "as above with B-D = 1", "source": "A"},
             "expected": {"dist": dA2["dist"], "path_A_to_F": path_to(dA2["prev"], "F")}, "tol": 0},
        ],
        [
            "Each step settles the unvisited node with the smallest tentative distance; that distance is then final",
            "Relaxation: a tentative distance is replaced only when a shorter path through the settled node is found",
            "Requires non-negative edge weights; a negative edge can invalidate an already settled distance",
            "The shortest path is reconstructed by following predecessor links back from the target",
            "Nodes are settled in non-decreasing order of distance",
            "Dijkstra's 1959 note also solves the minimum spanning tree problem (Problem 1)",
        ],
        [
            "Settling a node as soon as it is first reached (BFS behaviour) instead of when it is the minimum",
            "Updating distances of already settled nodes",
            "Allowing negative weights without warning",
            "Reported distance not equal to the sum of weights along the displayed path",
            "Treating the graph as directed when the example is undirected (or vice versa) without saying so",
        ],
        [
            q("Which node does Dijkstra's algorithm settle next?", ["The most recently discovered node",
              "The unsettled node with the smallest tentative distance", "The node with the most edges",
              "A random neighbour"], "B", 0),
            q("When is a tentative distance updated?", ["Every time a node is visited",
              "Only when a shorter path is found through the node just settled", "Never", "Only at the end"], "B", 1),
            q("Why can negative edge weights break the algorithm?", ["They make the graph disconnected",
              "A settled distance could later be improved, violating the 'final once settled' rule",
              "They slow down the priority queue", "They are not allowed in undirected graphs"], "B", 2),
            q("How is the shortest path itself recovered?", ["By sorting edges", "By following predecessor links back "
              "from the target", "By re-running BFS", "By summing all edges"], "B", 3),
            q("In what order are distances finalised?", ["Decreasing", "Random", "Non-decreasing", "Alphabetical"], "C", 4),
        ],
        invariants=["settled distances never change", "settle order has non-decreasing dist",
                    "dist[v] <= dist[u] + w(u,v) for every edge at termination"],
        benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_128", "subject": "comp_sci",
                   "subfield": "Graph Theory", "difficulty": "Medium", "targets": "computed here",
                   "input_format": "DOI URL (Springer)", "focus_style": "explicit numbered checks"},
    )

    # b2_kalman_1d -----------------------------------------------------------------------------------
    k_static = kalman_1d([1.0, 1.0, 1.0], x0=0.0, P0=1.0, R=1.0)
    k_noisy = kalman_1d([1.2, 0.9, 1.1, 1.0], x0=0.0, P0=1.0, R=0.5, Q=0.1)
    k_distrust = kalman_1d([5.0], x0=0.0, P0=1.0, R=100.0)
    Qs, Rs = 0.1, 0.5
    Pp_inf = (Qs + math.sqrt(Qs * Qs + 4 * Qs * Rs)) / 2
    case(
        "b2_kalman_1d",
        "https://arxiv.org/html/1910.03558v1",
        "Kalman filter, scalar (one-dimensional) case: show one predict step and one update step at a time for "
        "a constant or slowly drifting quantity measured with noise. Let the learner set the initial estimate "
        "and its variance, the process noise Q, the measurement noise R and type in measurements. Show the "
        "Kalman gain, the corrected estimate and its variance after each measurement, and how the gain "
        "settles to a steady value. Contrast trusting the sensor (small R) with trusting the model (large R).",
        "Second-year EE undergraduate who knows mean and variance but has not studied estimation theory.",
        """
Source: H. Masnadi-Shirazi, A. Masnadi-Shirazi and M.-A. Dastgheib, "A Step by Step Mathematical
Derivation and Tutorial on Kalman Filters", arXiv:1910.03558 (2019), checked against the arXiv HTML
rendering. Paraphrased; the scalar equations are the one-dimensional special case of the paper's
matrix results.

The tutorial derives the Kalman filter twice: as a minimum-variance (orthogonal projection) estimate
and as Bayesian optimal filtering. In the Bayesian view, filtering builds the posterior belief about
the state given all data so far in two recursive steps. Prediction propagates the previous belief
through the state model (Chapman-Kolmogorov equation). Update uses the new measurement via Bayes'
rule, multiplying the predicted belief by the likelihood of the measurement. For linear models with
Gaussian noise every belief stays Gaussian, so only a mean and a variance need to be tracked.

Scalar case with state model x_k = a x_(k-1) + w (variance Q) and measurement z_k = x_k + v
(variance R):
    predict:  x- = a x,      P- = a^2 P + Q
    gain:     K = P- / (P- + R)
    update:   x = x- + K (z - x-),    P = (1 - K) P-
K lies between 0 and 1: a precise sensor (small R) gives K near 1 and the estimate follows the
measurement; a noisy sensor (large R) gives K near 0 and the estimate stays near the prediction.
The updated variance is always smaller than both P- and R.
""",
        {"title": "A Step by Step Mathematical Derivation and Tutorial on Kalman Filters", "authors": "H. Masnadi-Shirazi, "
         "A. Masnadi-Shirazi, M.-A. Dastgheib", "year": 2019, "venue": "arXiv:1910.03558 [stat.OT]",
         "url": "https://arxiv.org/html/1910.03558v1", "section": "Part II, Bayesian optimal filtering; Sec. 6 Kalman filter",
         "verified": "arXiv HTML v1 fetched 2026-10-03; predict/update structure confirmed; scalar form derived here."},
        ["x- = a x; P- = a^2 P + Q", "K = P-/(P- + R)", "x = x- + K (z - x-); P = (1-K) P-",
         "steady state (a=1): P-_inf = (Q + sqrt(Q^2 + 4 Q R))/2, K_inf = P-_inf/(P-_inf + R)"],
        [
            {"label": "constant value, Q = 0", "params": {"x0": 0, "P0": 1, "R": 1, "Q": 0, "a": 1, "z": [1, 1, 1]},
             "expected": {"steps": k_static}, "tol": 1e-6, "note": "K = 1/2, 1/3, 1/4: a running average with the prior"},
            {"label": "drifting value", "params": {"x0": 0, "P0": 1, "R": 0.5, "Q": 0.1, "a": 1, "z": [1.2, 0.9, 1.1, 1.0]},
             "expected": {"steps": k_noisy, "steady_P_pred": Pp_inf, "steady_K": Pp_inf / (Pp_inf + Rs)}, "tol": 1e-4},
            {"label": "distrust sensor (R = 100)", "params": {"x0": 0, "P0": 1, "R": 100, "Q": 0, "z": [5]},
             "expected": {"steps": k_distrust}, "tol": 1e-6, "note": "estimate barely moves (K ~ 0.0099)"},
        ],
        [
            "Two-step recursion: predict with the model, then update with the measurement",
            "Kalman gain K = P-/(P- + R) weighs prediction against measurement and lies in [0, 1]",
            "Updated estimate x = x- + K (z - x-) (prediction plus gain times innovation)",
            "Updated variance P = (1 - K) P- is smaller than both the prior variance and R",
            "Small R -> trust the measurement (K -> 1); large R -> trust the prediction (K -> 0)",
            "With constant Q and R the gain converges to a steady-state value",
        ],
        [
            "Gain computed as R/(P- + R) (weights swapped)",
            "Forgetting to add Q in the predict step, so the filter becomes overconfident",
            "Variance increasing after an update",
            "Treating P as a standard deviation instead of a variance",
            "Claiming the Kalman filter needs a training dataset",
        ],
        [
            q("What is the Kalman gain in the scalar case?", ["R/(P- + R)", "P-/(P- + R)", "P- R", "1/R"], "B", 1),
            q("If the sensor is very noisy (R large), the gain is:", ["Close to 1", "Close to 0", "Exactly 0.5",
              "Negative"], "B", 4),
            q("After an update, the estimate variance:", ["Increases", "Is smaller than both P- and R",
              "Equals R", "Is unchanged"], "B", 3),
            q("What are the two steps of each Kalman filter cycle?", ["Train and test", "Predict and update",
              "Sort and merge", "Encode and decode"], "B", 0),
            q("The updated estimate equals:", ["z", "x- + K (z - x-)", "(x- + z)/2 always", "K z"], "B", 2),
        ],
        invariants=["0 <= K <= 1", "P_post <= min(P_pred, R)", "a=1, Q=0, z constant: estimate -> z"],
        benchmark={"name": "arXiv tutorial (no benchmark dataset)", "license": "arXiv perpetual non-exclusive (paraphrase only)",
                   "input_format": "arXiv HTML URL"},
    )

    # b2_dropout -------------------------------------------------------------------------------------
    h, w = [1.0, 2.0, 3.0, 4.0], [0.5, -1.0, 0.25, 1.0]
    case(
        "b2_dropout",
        "https://arxiv.org/pdf/1207.0580v1",
        "Improving neural networks by preventing co-adaptation of feature detectors (dropout). Explain, with one "
        "tiny layer of four units, what randomly omitting units during training does and why the weights are "
        "scaled at test time. Let the learner set the unit outputs, the weights and the retention probability, "
        "resample random masks, and compare the average over many masks with the scaled 'mean network'.",
        "First-year engineering student with no ML background.",
        """
Source: G. E. Hinton, N. Srivastava, A. Krizhevsky, I. Sutskever and R. R. Salakhutdinov, "Improving
neural networks by preventing co-adaptation of feature detectors", arXiv:1207.0580 (2012). Paraphrased
from the abstract and method description (arXiv PDF v1).

A large feedforward network trained on a small training set tends to overfit. The paper reduces this
by randomly omitting half of the hidden units ("feature detectors") on each training case. Because a
unit cannot rely on any particular other unit being present, complex co-adaptations are prevented,
and each unit must learn a feature that is useful in many different contexts. Each training case
therefore effectively trains a different "thinned" network; with n units there are 2^n possible
thinned networks, all sharing weights.

At test time all units are used, but their outgoing weights are halved (when units were kept with
probability one half) to compensate for the fact that twice as many are now active. The authors call
this the "mean network". With a single hidden layer and a softmax output, using the mean network is
equivalent to taking the geometric mean of the predictions of all 2^n thinned networks. In general,
if each unit is kept with probability p, the expected input a downstream unit receives during
training is p times its input in the full network, which is exactly what scaling the weights by p at
test time reproduces. The paper also reports that dropping 20% of the input units helps on some tasks.
Dropout gave large improvements on speech and object recognition benchmarks.
""",
        {"title": "Improving neural networks by preventing co-adaptation of feature detectors", "authors": "Hinton, "
         "Srivastava, Krizhevsky, Sutskever, Salakhutdinov", "year": 2012, "venue": "arXiv:1207.0580",
         "url": "https://arxiv.org/pdf/1207.0580v1", "section": "abstract; method; test-time 'mean network'",
         "verified": "arXiv abstract fetched via export API 2026-10-03; mean-network halving stated from the paper's "
                     "method section as widely reproduced."},
        ["mask m_i ~ Bernoulli(p_keep)", "train input s = sum m_i w_i h_i; E[s] = p_keep * sum w_i h_i",
         "test (mean network): weights scaled by p_keep", "Var[s] = p(1-p) sum (w_i h_i)^2", "number of thinned nets = 2^n"],
        [
            {"label": "p_keep = 0.5 (paper's hidden-unit rate)", "params": {"h": h, "w": w, "p_keep": 0.5},
             "expected": dropout_layer(h, w, 0.5), "tol": 1e-9},
            {"label": "p_keep = 0.8 (paper's input-unit rate)", "params": {"h": h, "w": w, "p_keep": 0.8},
             "expected": dropout_layer(h, w, 0.8), "tol": 1e-9},
            {"label": "no dropout", "params": {"h": h, "w": w, "p_keep": 1.0},
             "expected": dropout_layer(h, w, 1.0), "tol": 1e-9},
        ],
        [
            "During training each hidden unit is randomly omitted (the paper uses probability 0.5)",
            "Dropout prevents co-adaptation: units cannot rely on specific other units",
            "At test time all units are used with outgoing weights halved (scaled by the keep probability)",
            "Scaling makes the test-time input equal to the expected training-time input",
            "Each training case trains one of 2^n weight-sharing thinned networks; the mean network approximates "
            "their average",
            "Dropout reduces overfitting when a large network is trained on little data",
        ],
        [
            "Scaling weights by the drop probability instead of the keep probability",
            "Applying dropout (random masks) at test time",
            "Scaling at both training and test time (double compensation)",
            "Claiming the mean network is exactly equal to averaging thinned networks for deep nonlinear nets",
            "Saying dropout removes units permanently",
        ],
        [
            q("In the paper, what fraction of hidden units is omitted on each training case?", ["10%", "Half",
              "All but one", "None"], "B", 0),
            q("What problem does dropout prevent according to the title?", ["Vanishing gradients",
              "Co-adaptation of feature detectors", "Slow inference", "Data leakage"], "B", 1),
            q("What is done at test time?", ["Units are still dropped randomly", "All units are used and outgoing "
              "weights are halved", "Only half the units are used", "Weights are doubled"], "B", 2),
            q("If units are kept with probability p, the expected training input to the next unit is:",
              ["The full input", "p times the full input", "(1-p) times the full input", "Zero"], "B", 3),
            q("How many thinned networks exist for a layer of n units?", ["n", "2n", "n^2", "2^n"], "D", 4),
        ],
        invariants=["mean over all masks == p_keep * full input", "test input with scaled weights == expected train input"],
        benchmark={"name": "classic arXiv ML paper", "license": "arXiv (paraphrase only)", "input_format": "arXiv PDF URL"},
    )

    # b2_batchnorm -----------------------------------------------------------------------------------
    xb = [1.0, 2.0, 3.0, 4.0]
    bn_id = batchnorm(xb, gamma=math.sqrt(1.25 + 1e-5), beta=2.5)
    case(
        "b2_batchnorm",
        "https://arxiv.org/abs/1502.03167",
        "Batch Normalization, Algorithm 1 (the batch normalizing transform). Using one feature and a mini-batch of "
        "four numbers, show the mini-batch mean and variance, the normalized values, and the scale-and-shift "
        "output. Let the learner edit the batch, gamma, beta and epsilon. Show that the right gamma and beta "
        "recover the original inputs, and what happens when every value in the batch is equal.",
        "Mechatronics junior who has trained a small neural network once in a lab.",
        """
Source: S. Ioffe and C. Szegedy, "Batch Normalization: Accelerating Deep Network Training by Reducing
Internal Covariate Shift", arXiv:1502.03167 (ICML 2015). Paraphrased from the abstract and Section 3.

Training deep networks is complicated because the distribution of each layer's inputs changes as the
parameters of earlier layers change; the authors call this internal covariate shift. They make
normalization part of the architecture and perform it for each training mini-batch.

Algorithm 1 (Batch Normalizing Transform), applied to each activation x over a mini-batch
B = {x_1 ... x_m}:
    mu_B = (1/m) sum x_i                      (mini-batch mean)
    sigma_B^2 = (1/m) sum (x_i - mu_B)^2      (mini-batch variance)
    x_hat_i = (x_i - mu_B) / sqrt(sigma_B^2 + epsilon)
    y_i = gamma x_hat_i + beta                (scale and shift)
epsilon is a small constant added for numerical stability. gamma and beta are learned parameters.
Because simply normalizing could change what a layer can represent, the transform can represent the
identity: setting gamma = sqrt(Var[x]) and beta = E[x] recovers the original activations.

At inference the mini-batch statistics are replaced by population statistics so the output depends
only on the input; the paper uses the unbiased variance estimate m/(m-1) times the average mini-batch
variance. Batch Normalization allows much higher learning rates and less careful initialization, and
also acts as a regularizer, in some cases removing the need for Dropout.
""",
        {"title": "Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift",
         "authors": "S. Ioffe, C. Szegedy", "year": 2015, "venue": "ICML 2015 (arXiv:1502.03167)",
         "url": "https://arxiv.org/abs/1502.03167", "section": "Algorithm 1; Sec. 3.1 inference",
         "verified": "Abstract fetched via arXiv export API 2026-10-03; Algorithm 1 stated from the paper."},
        ["mu_B = mean(x)", "sigma_B^2 = (1/m) sum (x - mu_B)^2 (biased)", "x_hat = (x - mu_B)/sqrt(sigma_B^2 + eps)",
         "y = gamma x_hat + beta", "inference variance = m/(m-1) E[sigma_B^2]"],
        [
            {"label": "gamma=1, beta=0", "params": {"x": xb, "gamma": 1, "beta": 0, "eps": 1e-5},
             "expected": batchnorm(xb), "tol": 1e-4, "note": "output mean 0, variance ~1"},
            {"label": "gamma=2, beta=1", "params": {"x": xb, "gamma": 2, "beta": 1, "eps": 1e-5},
             "expected": batchnorm(xb, 2, 1), "tol": 1e-4},
            {"label": "identity recovery", "params": {"x": xb, "gamma": "sqrt(1.25 + 1e-5)", "beta": 2.5, "eps": 1e-5},
             "expected": {"y": bn_id["y"]}, "tol": 1e-6},
            {"label": "constant batch", "params": {"x": [3.0, 3.0, 3.0, 3.0], "gamma": 1, "beta": 0, "eps": 1e-5},
             "expected": batchnorm([3.0] * 4), "tol": 1e-9, "note": "variance 0; eps prevents division by zero; x_hat = 0"},
        ],
        [
            "Normalize with the mini-batch mean and variance: x_hat = (x - mu_B)/sqrt(sigma_B^2 + eps)",
            "Then scale and shift with learned gamma and beta: y = gamma x_hat + beta",
            "gamma = sqrt(Var), beta = mean recovers the original activations (identity is representable)",
            "epsilon is added for numerical stability (e.g. a constant batch has zero variance)",
            "At inference, population statistics replace mini-batch statistics",
            "Motivation: reduce internal covariate shift, allowing higher learning rates",
        ],
        [
            "Using the unbiased (1/(m-1)) variance in the training-time transform",
            "Normalizing across features instead of across the mini-batch for one feature",
            "Applying gamma and beta before normalization",
            "Division by zero for a constant batch (missing epsilon)",
            "Using mini-batch statistics at inference without noting the difference",
        ],
        [
            q("What is subtracted from each activation in batch normalization?", ["The minimum", "The mini-batch mean",
              "gamma", "The learning rate"], "B", 0),
            q("What do gamma and beta do?", ["Scale and shift the normalized value", "Set the learning rate",
              "Choose the batch size", "Drop units"], "A", 1),
            q("Which choice makes BN output equal its input?", ["gamma=0, beta=0", "gamma=sqrt(Var), beta=mean",
              "gamma=1, beta=0", "gamma=mean, beta=sqrt(Var)"], "B", 2),
            q("Why is epsilon added?", ["For numerical stability when the variance is tiny", "To add noise",
              "To speed up training", "To regularize gamma"], "A", 3),
            q("What is used at inference time?", ["The current mini-batch statistics", "Population statistics",
              "No normalization", "Random statistics"], "B", 4),
        ],
        invariants=["mean(x_hat) == 0", "var(x_hat) ~ 1 (slightly below because of eps)", "y == gamma*x_hat + beta"],
        benchmark={"name": "classic arXiv ML paper", "license": "arXiv (paraphrase only)", "input_format": "arXiv abs URL"},
    )

    # b2_pendulum ------------------------------------------------------------------------------------
    case(
        "b2_pendulum",
        "arXiv:0707.1029",
        "Period of a simple pendulum at finite amplitude: how wrong is the small-angle formula? Let the learner "
        "set the release angle and length, animate the swing, and compare the exact period with 2 pi sqrt(L/g) "
        "and with the first correction terms of the series.",
        "Mechanical engineering senior.",
        """
Source: arXiv:0707.1029, "Systematic approximations for the period of a finite amplitude pendulum"
(2007). Paraphrased from the abstract, plus the standard result it builds on.

For small swings a simple pendulum of length L obeys d^2 theta/dt^2 = -(g/L) sin theta ~ -(g/L) theta,
which gives simple harmonic motion with period T0 = 2 pi sqrt(L/g), independent of amplitude. For a
release angle theta0 that is not small, the exact period is

    T = 4 sqrt(L/g) K(k),   k = sin(theta0/2),

where K is the complete elliptic integral of the first kind; equivalently T = T0 / AGM(1, cos(theta0/2))
using the arithmetic-geometric mean. The period always grows with amplitude and diverges as theta0
approaches 180 degrees (the inverted position). Expanding in the amplitude gives the standard series

    T = T0 (1 + theta0^2/16 + 11 theta0^4/3072 + ...),

so at 10 degrees the small-angle formula is off by only about 0.2 percent, but at 90 degrees by about
15 percent. The paper notes that truncating the standard series (written in terms of the energy, and
hence amplitude) always gives a lower limit on the period, because every term is positive. Adjusting
the last kept term to account for the dropped terms gives an upper limit, and intermediate expressions
then give more accurate approximations.
""",
        {"title": "Systematic approximations for the period of a finite amplitude pendulum", "authors": "see arXiv:0707.1029",
         "year": 2007, "venue": "arXiv:0707.1029", "url": "https://arxiv.org/abs/0707.1029",
         "section": "abstract; standard elliptic-integral result", "verified": "Abstract fetched via arXiv export API "
                                                                            "2026-10-03; exact period computed via AGM here."},
        ["T0 = 2 pi sqrt(L/g)", "T = T0 / AGM(1, cos(theta0/2)) = 4 sqrt(L/g) K(sin(theta0/2))",
         "T ~ T0 (1 + theta0^2/16 + 11 theta0^4/3072)", "error % = 100 (T - T0)/T"],
        [{"label": f"theta0 = {a} deg, L = 1 m, g = 9.81", "params": {"theta0_deg": a, "L_m": 1.0, "g": 9.81},
          "expected": pendulum(a), "tol": 1e-4} for a in (10, 30, 60, 90, 170)],
        [
            "Small-angle period T0 = 2 pi sqrt(L/g) comes from approximating sin theta by theta",
            "The exact period depends on amplitude and is longer than T0 (elliptic integral K(sin(theta0/2)))",
            "Series: T = T0 (1 + theta0^2/16 + 11 theta0^4/3072 + ...)",
            "Small-angle error is about 0.2% at 10 degrees and about 15-18% at 90 degrees",
            "The period diverges as the amplitude approaches 180 degrees",
            "A truncated series is a lower bound on the period because all terms are positive",
        ],
        [
            "Using degrees inside theta0^2/16",
            "Claiming the period is independent of amplitude at large angles",
            "Shorter period at larger amplitude",
            "Animating with the linearized equation while labelling it 'exact'",
            "Period depending on the bob's mass",
        ],
        [
            q("What is the small-angle period?", ["2 pi sqrt(g/L)", "2 pi sqrt(L/g)", "pi sqrt(L/g)", "sqrt(L g)"], "B", 0),
            q("How does the true period change as amplitude increases?", ["It decreases", "It stays the same",
              "It increases", "It oscillates"], "C", 1),
            q("What is the first correction factor in the period series?", ["1 + theta0/2", "1 + theta0^2/16",
              "1 - theta0^2/16", "1 + theta0^2/4"], "B", 2),
            q("Roughly how large is the small-angle error at 10 degrees?", ["0.2%", "5%", "15%", "50%"], "A", 3),
            q("Why does a truncated series give a lower bound on the period?", ["Because all its terms are positive",
              "Because g varies", "Because of air drag", "It gives an upper bound"], "A", 5),
        ],
        invariants=["T >= T0", "T increasing in theta0", "T -> inf as theta0 -> 180 deg", "T independent of mass"],
        benchmark={"name": "arXiv physics paper (no benchmark dataset)", "license": "arXiv (paraphrase only)",
                   "input_format": "bare arXiv id"},
    )

    # b2_carnot --------------------------------------------------------------------------------------
    case(
        "b2_carnot",
        "https://archive.org/details/reflectionsonmot00carnrich",
        "Carnot (1824), Reflections on the Motive Power of Heat. Explain why no heat engine working between two "
        "temperatures can beat the reversible (Carnot) engine, and compute that limit. Let the learner set the "
        "hot and cold reservoir temperatures and the heat drawn from the hot reservoir. Show the four steps of "
        "the cycle on a P-V or T-S sketch, the work output, the heat rejected and the efficiency limit. Make "
        "clear which parts are Carnot's 1824 argument and which are the later Kelvin-scale formula, and guard "
        "against entering temperatures in Celsius.",
        "Mechanical engineering senior who has taken one thermodynamics course.",
        """
Benchmark provenance: TheoremExplainBench theorem_112 "Carnot cycle" (physics / Thermodynamics, Medium),
MIT licence: an ideal thermodynamic cycle proposed by Sadi Carnot in 1824. Primary source: S. Carnot,
Reflexions sur la puissance motrice du feu (Paris, 1824); English translation by R. H. Thurston (1890),
public domain, archive.org item reflectionsonmot00carnrich. Paraphrased.

Carnot asked whether the motive power (work) obtainable from heat is limited, and whether it depends
on the working substance. He argued that work is produced when heat passes from a hot body to a cold
body, and described an ideal cycle: (1) expansion in contact with the hot body at constant
temperature, (2) further expansion with no heat exchange until the substance cools to the cold body's
temperature, (3) compression at the cold temperature while heat is given up, and (4) compression with
no heat exchange back to the hot temperature. Because every step can be reversed, no engine can
produce more work from the same heat between the same two temperatures; otherwise one could combine
it with a reversed Carnot engine and create work from nothing. Hence the maximum work depends only on
the two temperatures, not on the working substance.

Carnot reasoned with the caloric theory. The quantitative form came later, from Clausius and Kelvin
using absolute temperature: efficiency = W/Q_h = 1 - T_c/T_h, with Q_h/T_h = Q_c/T_c for the
reversible cycle.
""",
        {"title": "Reflections on the Motive Power of Heat (transl. R. H. Thurston)", "authors": "S. Carnot", "year": 1824,
         "venue": "Bachelier, Paris (1824); Wiley (1890 translation)", "url":
         "https://archive.org/details/reflectionsonmot00carnrich", "section": "the reversible cycle argument",
         "verified": "archive.org item located via advancedsearch API 2026-10-03; text not re-read; efficiency formula "
                     "attributed to Clausius/Kelvin (honesty check)."},
        ["eta = 1 - Tc/Th (kelvin)", "W = eta Qh; Qc = Qh - W", "Qh/Th = Qc/Tc (reversible)", "COP_ref = Tc/(Th - Tc)"],
        [
            {"label": "Th 500 K, Tc 300 K", "params": {"Th_K": 500, "Tc_K": 300, "Qh_J": 1000}, "expected": carnot(500, 300),
             "tol": 1e-6},
            {"label": "power plant 800 K / 300 K", "params": {"Th_K": 800, "Tc_K": 300, "Qh_J": 1000},
             "expected": carnot(800, 300), "tol": 1e-6},
            {"label": "Celsius trap: 227 C and 27 C", "params": {"Th_C": 227, "Tc_C": 27, "Qh_J": 1000},
             "expected": {"efficiency_correct_kelvin": carnot(500.15, 300.15)["efficiency"],
                          "efficiency_wrong_celsius": 1 - 27 / 227}, "tol": 1e-4},
            {"label": "equal temperatures", "params": {"Th_K": 300, "Tc_K": 300, "Qh_J": 1000},
             "expected": {"efficiency": 0.0, "W_J": 0.0}, "tol": 1e-9},
        ],
        [
            "Carnot efficiency limit: eta = 1 - Tc/Th with absolute temperatures",
            "The cycle has four reversible steps: two isothermal and two adiabatic",
            "No engine between the same two reservoirs can be more efficient than a reversible engine",
            "The limit depends only on the reservoir temperatures, not the working substance",
            "Carnot's 1824 argument used caloric theory; the 1 - Tc/Th formula came later (Clausius, Kelvin)",
            "Work requires a temperature difference: Th = Tc gives zero work",
        ],
        [
            "Using Celsius temperatures in 1 - Tc/Th",
            "Efficiency of 100% or more",
            "Attributing the formula 1 - Tc/Th directly to Carnot's 1824 text",
            "Swapping the order of the isothermal and adiabatic steps",
            "Claiming real engines can reach the Carnot limit",
        ],
        [
            q("What is the Carnot efficiency?", ["Tc/Th", "1 - Tc/Th", "1 - Th/Tc", "Th - Tc"], "B", 0),
            q("Which temperatures must be used in the formula?", ["Celsius", "Fahrenheit", "Absolute (kelvin)", "Any"], "C", 0),
            q("What does the Carnot limit depend on?", ["The working gas", "Only the two reservoir temperatures",
              "The engine size", "The fuel price"], "B", 3),
            q("What are the four steps of the Carnot cycle?", ["Two isothermal and two adiabatic", "Four isobaric",
              "Two isochoric and two isobaric", "Four adiabatic"], "A", 1),
            q("Which theory of heat did Carnot use in 1824?", ["Kinetic theory", "Caloric theory", "Quantum theory",
              "Statistical mechanics"], "B", 4),
        ],
        invariants=["0 <= eta < 1", "Qh/Th == Qc/Tc", "W + Qc == Qh"],
        benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_112", "subject": "physics",
                   "subfield": "Thermodynamics", "difficulty": "Medium", "targets": "computed here",
                   "input_format": "non-arXiv archive URL (public-domain book)", "focus_style": "long and detailed"},
    )

    # b2_arrhenius -----------------------------------------------------------------------------------
    r_10K = math.exp(50000 / R_GAS * (1 / 298.15 - 1 / 308.15))
    k1, k2 = 1.0e-3, 4.0e-3
    Ea_two_point = R_GAS * math.log(k2 / k1) / (1 / 300 - 1 / 320)
    case(
        "b2_arrhenius",
        "https://doi.org/10.1515/zpch-1889-0416",
        "Arrhenius equation: how strongly temperature speeds up a reaction. Let the learner set the activation "
        "energy, pre-exponential factor and temperature; show k(T), the ratio for a 10 K rise and the straight "
        "line of ln k against 1/T, and let them recover Ea from two measured rate constants.",
        "Food science undergraduate who studies shelf life and knows logarithms.",
        """
Benchmark provenance: TheoremExplainBench theorem_147 "Arrhenius Equation" (chemistry / Chemical
Kinetics, Medium), MIT licence: a formula for the temperature dependence of reaction rates. Primary
source: S. Arrhenius, "Uber die Reaktionsgeschwindigkeit bei der Inversion von Rohrzucker durch
Sauren", Zeitschrift fur physikalische Chemie 4 (1889) 226-248. Paraphrased.

Arrhenius studied how fast cane sugar (sucrose) is inverted by acids and found that the rate rises
far faster with temperature than the speed of the molecules or the number of collisions would
suggest. He explained this by assuming that only a small fraction of "active" molecules can react,
and that this fraction is in equilibrium with the ordinary molecules. Its temperature dependence
follows van 't Hoff's equation for equilibria, so it grows exponentially. In modern form

    k = A exp(-Ea / (R T)),

where Ea is the activation energy, A the pre-exponential factor, R = 8.314 J/(mol K) and T is the
absolute temperature. Taking logarithms, ln k = ln A - (Ea/R)(1/T), so a plot of ln k against 1/T
is a straight line with slope -Ea/R. From two rate constants, Ea = R ln(k2/k1) / (1/T1 - 1/T2). For
activation energies around 50 kJ/mol, a 10 K rise near room temperature roughly doubles the rate,
which is the origin of the familiar rule of thumb.
""",
        {"title": "Uber die Reaktionsgeschwindigkeit bei der Inversion von Rohrzucker durch Sauren", "authors": "S. Arrhenius",
         "year": 1889, "venue": "Z. Phys. Chem. 4:226-248", "url": "https://doi.org/10.1515/zpch-1889-0416",
         "section": "active-molecule hypothesis", "verified": "DOI reachable (202) 2026-10-03; content paraphrased from "
                                                             "standard accounts; TEB description via HF API."},
        ["k = A exp(-Ea/(R T))", "k2/k1 = exp((Ea/R)(1/T1 - 1/T2))", "ln k = ln A - (Ea/R)(1/T)",
         "Ea = R ln(k2/k1)/(1/T1 - 1/T2)"],
        [
            {"label": "10 K rise, Ea = 50 kJ/mol", "params": {"Ea_J_per_mol": 50000, "T1_K": 298.15, "T2_K": 308.15},
             "expected": {"ratio_k2_over_k1": r_10K}, "tol": 1e-3},
            {"label": "absolute k", "params": {"A_per_s": 1e13, "Ea_J_per_mol": 100000, "T_K": 298.15},
             "expected": {"k_per_s": arrhenius_k(1e13, 100000, 298.15), "slope_ln_k_vs_invT_K": -100000 / R_GAS},
             "tol": 1e-3},
            {"label": "two-point Ea", "params": {"k1": k1, "T1_K": 300, "k2": k2, "T2_K": 320},
             "expected": {"Ea_J_per_mol": Ea_two_point}, "tol": 1e-3},
            {"label": "Ea = 0", "params": {"A_per_s": 1e6, "Ea_J_per_mol": 0, "T_K": 350},
             "expected": {"k_per_s": 1e6}, "tol": 1e-9, "note": "no temperature dependence"},
        ],
        [
            "k = A exp(-Ea/(RT)) with absolute temperature",
            "ln k against 1/T is a straight line with slope -Ea/R (Arrhenius plot)",
            "Higher activation energy means stronger temperature sensitivity",
            "For Ea ~ 50 kJ/mol a 10 K rise near room temperature roughly doubles the rate",
            "Arrhenius explained it with a small fraction of 'active' molecules in equilibrium with ordinary ones",
            "Ea can be found from two rate constants at two temperatures",
        ],
        [
            "Using Celsius in the exponent",
            "Using R in kJ while Ea is in J (factor 1000 error)",
            "Plotting ln k against T instead of 1/T and claiming a straight line",
            "Sign error making the rate fall with temperature",
            "Treating the 'doubling per 10 K' rule as exact for every Ea",
        ],
        [
            q("What is the Arrhenius equation?", ["k = A exp(-Ea/(RT))", "k = A Ea T", "k = A exp(RT/Ea)", "k = Ea/(RT)"], "A", 0),
            q("Which plot gives a straight line?", ["k vs T", "ln k vs 1/T", "k vs 1/T", "ln k vs T^2"], "B", 1),
            q("What is the slope of that line?", ["Ea/R", "-Ea/R", "ln A", "-RT"], "B", 1),
            q("A larger activation energy makes the rate:", ["Less sensitive to temperature", "More sensitive to temperature",
              "Independent of temperature", "Negative"], "B", 2),
            q("How did Arrhenius explain the strong temperature effect?", ["Faster molecular speeds alone",
              "A small fraction of 'active' molecules in equilibrium with ordinary ones", "Catalyst decay",
              "Quantum tunnelling"], "B", 4),
        ],
        invariants=["k increasing in T for Ea > 0", "k -> A as T -> inf", "ln k linear in 1/T"],
        benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_147", "subject": "chemistry",
                   "subfield": "Chemical Kinetics", "difficulty": "Medium", "targets": "computed here",
                   "input_format": "DOI URL (De Gruyter)"},
    )

    # b2_nash_2x2 ------------------------------------------------------------------------------------
    PD = ([[3, 0], [5, 1]], [[3, 5], [0, 1]])
    MP = ([[1, -1], [-1, 1]], [[-1, 1], [1, -1]])
    BoS = ([[2, 0], [0, 1]], [[1, 0], [0, 2]])
    case(
        "b2_nash_2x2",
        "https://doi.org/10.1073/pnas.36.1.48",
        "Nash (1950), Equilibrium Points in N-Person Games, specialised to two players with two strategies each. "
        "Build an interactive 2x2 payoff matrix where the learner edits both players' payoffs and sees, for "
        "every cell, whether either player wants to deviate (best-response arrows). Mark all pure-strategy "
        "equilibria. Then let the learner drag each player's mixing probability and show both expected payoffs, "
        "highlighting the mixed equilibrium where each player is indifferent between their two strategies. "
        "Provide presets for the prisoner's dilemma (one equilibrium that is worse for both than cooperating), "
        "matching pennies (no pure equilibrium, only a 50/50 mixed one) and battle of the sexes (two pure "
        "equilibria plus a mixed one). Check that at a mixed equilibrium neither player can gain by changing "
        "their own probability alone, and state that Nash proved at least one equilibrium (possibly mixed) "
        "always exists in finite games.",
        "Economics sophomore who knows expected value but no game theory.",
        """
Source: J. F. Nash, "Equilibrium Points in N-Person Games", Proceedings of the National Academy of
Sciences 36 (1950) 48-49. Paraphrased (publisher returns 403 to scripts; content is the well-known
one-page note).

Nash considers games with n players, each with a finite set of pure strategies, and allows mixed
strategies: probability distributions over a player's pure strategies. Payoffs of mixed strategies
are expected values. An n-tuple of strategies (one per player) is an equilibrium point if each
player's mixed strategy maximizes their own payoff when the other players' strategies are held fixed;
in other words, each strategy is a best response to the others. Nash maps each n-tuple to the set of
n-tuples that respond best to it. Using Kakutani's fixed-point theorem, he shows a fixed point always
exists, so every finite game has at least one equilibrium point in mixed strategies.

For a 2x2 game this means: a cell is a pure equilibrium if neither player can raise their own payoff
by switching alone. If there is no pure equilibrium (as in matching pennies), the equilibrium is mixed.
At an interior mixed equilibrium each player randomises so that the OTHER player is indifferent
between their two strategies. Equilibria need not be efficient: in the prisoner's dilemma both
players defect even though mutual cooperation pays both more.
""",
        {"title": "Equilibrium Points in N-Person Games", "authors": "J. F. Nash", "year": 1950, "venue": "PNAS 36(1):48-49",
         "url": "https://doi.org/10.1073/pnas.36.1.48", "section": "whole note",
         "verified": "DOI returns 403 to scripts (2026-10-03); content stated from the well-known note."},
        ["pure NE: A[i][j] >= A[1-i][j] and B[i][j] >= B[i][1-j]",
         "row mix p(T) = (B[1][1]-B[1][0]) / ((B[0][0]-B[0][1]) - (B[1][0]-B[1][1]))",
         "column mix q(L) = (A[1][1]-A[0][1]) / ((A[0][0]-A[1][0]) - (A[0][1]-A[1][1]))"],
        [
            {"label": "prisoner's dilemma (T=C, B=D)", "params": {"row_payoffs": PD[0], "col_payoffs": PD[1]},
             "expected": dict(nash_2x2(*PD), equilibrium_payoffs=[1, 1], cooperative_payoffs=[3, 3]), "tol": 1e-9},
            {"label": "matching pennies", "params": {"row_payoffs": MP[0], "col_payoffs": MP[1]},
             "expected": nash_2x2(*MP), "tol": 1e-9},
            {"label": "battle of the sexes", "params": {"row_payoffs": BoS[0], "col_payoffs": BoS[1]},
             "expected": nash_2x2(*BoS), "tol": 1e-6},
        ],
        [
            "An equilibrium point: each player's strategy is a best response to the others' strategies",
            "Mixed strategies are probability distributions over pure strategies; payoffs are expected values",
            "Nash proved every finite game has at least one equilibrium (possibly mixed), via a fixed-point theorem",
            "At a mixed equilibrium each player makes the other indifferent between their strategies",
            "Matching pennies has no pure equilibrium; its equilibrium is 50/50",
            "The prisoner's dilemma equilibrium (defect, defect) is worse for both than mutual cooperation",
        ],
        [
            "Choosing a player's mix to make THEMSELVES indifferent (instead of the opponent)",
            "Claiming matching pennies has no equilibrium at all",
            "Confusing Nash equilibrium with the outcome that maximises total payoff",
            "Missing one of the two pure equilibria in battle of the sexes",
            "Reading payoffs from the wrong player's matrix",
        ],
        [
            q("What defines a Nash equilibrium?", ["Total payoff is maximal", "Each strategy is a best response to the "
              "others'", "Both players get equal payoffs", "Players cooperate"], "B", 0),
            q("What did Nash prove for finite games?", ["A pure equilibrium always exists",
              "At least one equilibrium in mixed strategies exists", "Equilibria are unique",
              "Equilibria are always efficient"], "B", 2),
            q("What is the equilibrium of matching pennies?", ["Both play heads", "No equilibrium",
              "Each mixes 50/50", "Row plays heads, column tails"], "C", 4),
            q("At an interior mixed equilibrium, each player's probabilities are chosen so that:",
              ["They maximise their own pure payoff", "The other player is indifferent between their strategies",
               "Payoffs are zero", "They always cooperate"], "B", 3),
            q("In the prisoner's dilemma the equilibrium outcome is:", ["Better for both than cooperation",
              "Worse for both than mutual cooperation", "Mixed 50/50", "Not defined"], "B", 5),
        ],
        invariants=["at mixed NE, row expected payoff equal for T and B", "pure NE cells have no profitable unilateral deviation"],
        benchmark={"name": "classic paper (no benchmark dataset)", "license": "n/a (paraphrase only)",
                   "input_format": "DOI URL (PNAS)", "focus_style": "long and detailed"},
    )

    # b2_pagerank ------------------------------------------------------------------------------------
    L4 = {"A": ["B", "C"], "B": ["C"], "C": ["A"], "D": ["C"]}
    case(
        "b2_pagerank",
        "http://infolab.stanford.edu/pub/papers/google.pdf",
        "PageRank, Section 2.1 of The Anatomy of a Large-Scale Hypertextual Web Search Engine. Explain the random "
        "surfer and the damping factor on a web of four pages. Let the learner add or remove links, change d, "
        "and step the power iteration one round at a time until the ranks stop changing. Check the formula's "
        "normalisation: the paper's printed form makes ranks sum to the number of pages, not to one.",
        "Information systems student comfortable with fractions and simple matrices.",
        """
Source: S. Brin and L. Page, "The Anatomy of a Large-Scale Hypertextual Web Search Engine", Computer
Networks and ISDN Systems 30 (1998) 107-117; Stanford InfoLab PDF, Section 2.1. Paraphrased, with one
short quotation; text extracted from the PDF on 2026-10-03.

Counting backlinks approximates a page's importance. PageRank refines this by not counting all links
equally and by normalizing by the number of links on a page. If pages T1...Tn point to page A, C(T) is
the number of links going out of page T, and d is a damping factor between 0 and 1 (usually 0.85),

    PR(A) = (1 - d) + d (PR(T1)/C(T1) + ... + PR(Tn)/C(Tn)).

The paper adds that the PageRanks "form a probability distribution over web pages". (With the formula as
printed the ranks sum to the number of pages; dividing (1 - d) by the number of pages N gives the
version that sums to one. The two differ only by the factor N.) PageRank can be computed with a
simple iterative algorithm and corresponds to the principal eigenvector of the normalized link
matrix.

Intuitive justification: a "random surfer" starts on a random page and keeps clicking links, never
going back, but eventually gets bored and jumps to a random page. The probability of visiting a page
is its PageRank, and the damping factor d is the probability of continuing to click rather than
jumping. A page ranks highly if many pages link to it, or if a few highly ranked pages do.
""",
        {"title": "The Anatomy of a Large-Scale Hypertextual Web Search Engine", "authors": "S. Brin, L. Page", "year": 1998,
         "venue": "Computer Networks and ISDN Systems 30:107-117", "url": "http://infolab.stanford.edu/pub/papers/google.pdf",
         "section": "2.1.1, 2.1.2", "verified": "PDF fetched and text extracted 2026-10-03; formula, d = 0.85 and the "
                                                "'probability distribution' sentence confirmed."},
        ["PR(A) = (1-d) + d sum PR(T)/C(T) (sums to N)", "normalised: PR(A) = (1-d)/N + d sum PR(T)/C(T) (sums to 1)",
         "power iteration from uniform start until change < tol"],
        [
            {"label": "4 pages, d = 0.85", "params": {"links": L4, "d": 0.85}, "expected": pagerank(L4, 0.85), "tol": 1e-4},
            {"label": "same graph, d = 0.5", "params": {"links": L4, "d": 0.5}, "expected": pagerank(L4, 0.5), "tol": 1e-4},
            {"label": "d = 0 (pure random jumps)", "params": {"links": L4, "d": 0.0}, "expected": pagerank(L4, 0.0),
             "tol": 1e-9, "note": "uniform ranks"},
            {"label": "3-cycle A->B->C->A", "params": {"links": {"A": ["B"], "B": ["C"], "C": ["A"]}, "d": 0.85},
             "expected": pagerank({"A": ["B"], "B": ["C"], "C": ["A"]}, 0.85), "tol": 1e-9, "note": "symmetric: 1/3 each"},
        ],
        [
            "PR(A) = (1-d) + d sum PR(T_i)/C(T_i): each linking page shares its rank equally among its out-links",
            "Damping factor d is usually 0.85",
            "Random surfer model: follow a link with probability d, jump to a random page otherwise",
            "Computed by simple iteration; equals the principal eigenvector of the normalised link matrix",
            "As printed, the formula's ranks sum to N; using (1-d)/N makes them a probability distribution",
            "A page ranks highly if many pages, or a few highly ranked pages, link to it",
        ],
        [
            "Dividing by the number of in-links instead of the linking page's out-links",
            "Claiming the printed formula sums to 1 without normalising",
            "Rank leaking away at pages with no out-links (dangling nodes) without handling it",
            "Stopping after one iteration and calling it converged",
            "Treating d as the probability of jumping instead of following a link",
        ],
        [
            q("In PR(A), each linking page T contributes:", ["PR(T)", "PR(T)/C(T)", "C(T)", "PR(T) C(T)"], "B", 0),
            q("What value of d does the paper usually use?", ["0.15", "0.5", "0.85", "1"], "C", 1),
            q("In the random surfer model, d is the probability of:", ["Jumping to a random page",
              "Following a link on the current page", "Pressing back", "Leaving the web"], "B", 2),
            q("How is PageRank computed?", ["Sorting by backlinks", "A simple iterative algorithm (principal eigenvector)",
              "Training a neural network", "Counting words"], "B", 3),
            q("With the formula exactly as printed, the ranks sum to:", ["1", "The number of pages", "d", "0"], "B", 4),
        ],
        invariants=["normalised ranks sum to 1", "d = 0 gives uniform ranks", "PR >= (1-d)/N"],
        benchmark={"name": "classic paper (no benchmark dataset)", "license": "n/a (paraphrase only)",
                   "input_format": "non-arXiv PDF URL (Stanford InfoLab)"},
    )

    # ==============================================================================================
    # PaperVoyager-inspired cases (pv_*): topics from arXiv:2603.22999, original papers cited.
    # ==============================================================================================
    pv_bench = lambda fmt: {"name": "PaperVoyager (topic only; data not released)", "paper": "arXiv:2603.22999",
                            "license": "n/a (no data used)", "input_format": fmt}

    # pv_raft_election ---------------------------------------------------------------------------
    def majority(n):
        return n // 2 + 1
    case(
        "pv_raft_election",
        "https://www.usenix.org/system/files/conference/atc14/atc14-paper-ongaro.pdf",
        "In Search of an Understandable Consensus Algorithm (Raft), Section 5.2 Leader election. Simulate a "
        "small cluster step by step: followers with randomized election timeouts, a follower that times out "
        "becomes a candidate, increments its term, votes for itself and requests votes. Let the learner set "
        "the cluster size and each server's timeout, crash the leader, and step through message delivery. "
        "Show each server's state, current term and vote. Check that at most one leader is elected per term, "
        "that a majority is required, and show a split vote resolved by a new term.",
        "Third-year software engineering student who has written multithreaded code but never studied consensus.",
        """
Source: D. Ongaro and J. Ousterhout, "In Search of an Understandable Consensus Algorithm", 2014 USENIX
Annual Technical Conference, pp. 305-319; PDF text extracted from usenix.org on 2026-10-03.
Paraphrased, with one short quotation.

Each Raft server is a follower, a candidate or a leader. Time is divided into terms numbered with
consecutive integers; each term begins with an election, and each server stores its current term.
Leaders send periodic heartbeats. If a follower hears nothing for an election timeout, it assumes
there is no leader: it increments its current term, becomes a candidate, votes for itself and sends
RequestVote RPCs to all other servers. A candidate wins if it receives votes from a majority of the
servers in the full cluster for the same term. Each server votes for at most one candidate per term,
first come first served, so at most one candidate can win a given term (Election Safety). A server
that sees a higher term updates its own term and reverts to follower; requests carrying a stale term
are rejected.

If several followers become candidates at once, votes may split so that nobody gets a majority; each
candidate then times out and starts a new election with a higher term. To make split votes rare,
"election timeouts are chosen randomly from a fixed interval (e.g., 150-300ms)", so usually one server
times out first, wins, and sends heartbeats before any other server times out. Section 5.4 adds a
restriction: a vote is only granted to a candidate whose log is at least as up to date.
""",
        {"title": "In Search of an Understandable Consensus Algorithm", "authors": "D. Ongaro, J. Ousterhout", "year": 2014,
         "venue": "USENIX ATC 2014, pp. 305-319", "url": "https://www.usenix.org/system/files/conference/atc14/atc14-paper-ongaro.pdf",
         "section": "5.1 Raft basics, 5.2 Leader election, Figure 2 RequestVote",
         "verified": "PDF fetched and text extracted 2026-10-03; majority rule, one vote per term, 150-300 ms and "
                     "split-vote handling confirmed."},
        ["majority(N) = floor(N/2) + 1", "tolerated failures = floor((N-1)/2)",
         "first candidate = server with smallest remaining timeout", "candidate: term += 1, votedFor = self"],
        [
            {"label": "5 servers, staggered timeouts, all followers at term 0",
             "params": {"N": 5, "timeouts_ms": [150, 220, 180, 300, 260], "one_way_delay_ms": 10},
             "expected": {"first_candidate": "S1", "candidate_term": 1, "majority": majority(5),
                          "votes_received": 5, "leader": "S1", "leader_elected_at_ms": 170,
                          "note_check": "S1 times out at 150, requests arrive at 160 (all others still followers), "
                                        "grants return at 170"}, "tol": 0},
            {"label": "split vote, 4 servers",
             "params": {"N": 4, "scenario": "S1 and S2 time out simultaneously in term 1; S3 receives S1's request "
                        "first, S4 receives S2's first"},
             "expected": {"votes_S1": 2, "votes_S2": 2, "majority": majority(4), "leader_in_term_1": None,
                          "next_election_term": 2}, "tol": 0},
            {"label": "majority sizes", "params": {"N": [3, 5, 7]},
             "expected": {"majority": [majority(n) for n in (3, 5, 7)],
                          "tolerated_failures": [(n - 1) // 2 for n in (3, 5, 7)]}, "tol": 0},
            {"label": "stale leader rejoins", "params": {"old_leader_term": 1, "cluster_term": 3},
             "expected": {"old_leader_becomes": "follower", "old_leader_new_term": 3}, "tol": 0},
        ],
        [
            "Servers are followers, candidates or leaders; time is divided into numbered terms",
            "A follower that receives no heartbeat within its election timeout becomes a candidate, increments its "
            "term and votes for itself",
            "A candidate needs votes from a majority of the full cluster; each server votes at most once per term",
            "At most one leader can be elected in a given term (Election Safety)",
            "Randomized election timeouts (e.g. 150-300 ms) make split votes rare; a split vote leads to a new term",
            "A server seeing a higher term updates its term and steps down to follower",
        ],
        [
            "Majority counted among live servers instead of the full cluster",
            "A server voting for two candidates in the same term",
            "Two leaders in the same term",
            "Candidate not incrementing its term or not voting for itself",
            "Fixed (non-random) timeouts presented as Raft's mechanism",
        ],
        [
            q("What does a follower do when its election timeout expires?", ["Shuts down", "Becomes a candidate, "
              "increments its term and votes for itself", "Becomes leader immediately", "Waits forever"], "B", 1),
            q("How many votes does a candidate need in a 5-server cluster?", ["2", "3", "4", "5"], "B", 2),
            q("How many leaders can be elected in one term?", ["At most one", "Two", "One per server", "Unlimited"], "A", 3),
            q("Why are election timeouts randomized?", ["To save power", "To make split votes rare",
              "To encrypt messages", "To order log entries"], "B", 4),
            q("What happens when a leader sees a higher term?", ["It ignores it", "It steps down to follower and adopts "
              "the higher term", "It doubles its term", "It deletes its log"], "B", 5),
        ],
        invariants=["<= 1 leader per term", "each server votes <= 1 time per term", "terms never decrease"],
        benchmark=pv_bench("non-arXiv conference PDF URL (USENIX)"),
    )

    # pv_cpu_scheduling --------------------------------------------------------------------------
    sil = [24, 3, 3]
    ost = [100, 10, 10]
    case(
        "pv_cpu_scheduling",
        "https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched.pdf",
        "Compare FIFO, shortest-job-first and round-robin CPU scheduling on a few jobs that all arrive at time 0. "
        "Let the learner set burst lengths and the round-robin time slice, draw the Gantt chart for each policy, "
        "and show per-job waiting, turnaround and response times with their averages.",
        "Information technology diploma student in an operating systems course.",
        """
Source: R. H. Arpaci-Dusseau and A. C. Arpaci-Dusseau, Operating Systems: Three Easy Pieces, Chapter 7
"Scheduling: Introduction" (free online textbook, version 1.10). Paraphrased from the PDF text
extracted on 2026-10-03.

The chapter introduces scheduling policies through simple workloads. Turnaround time is completion
time minus arrival time; response time is the time from arrival to the first time the job runs.
Waiting time is time spent ready but not running (turnaround minus run time when all jobs arrive at 0).

FIFO (first come, first served) is simple, but suffers from the convoy effect: if a long job A (100 s)
arrives just before two short jobs B and C (10 s each), the short jobs wait behind it and average
turnaround is (100 + 110 + 120)/3 = 110 s. Shortest Job First runs the shortest job first; with all
jobs arriving together it is optimal for average turnaround, here (10 + 20 + 120)/3 = 50 s. Its
preemptive version (STCF) handles later arrivals.

Both are poor for response time on interactive systems, because the last job waits for all others to
finish. Round Robin runs each job for a time slice (scheduling quantum) and then switches to the next
job in the queue. With a short slice, response time is excellent, but turnaround time is among the
worst, because every job is stretched out. The slice length trades responsiveness against the cost
of context switching.
""",
        {"title": "Operating Systems: Three Easy Pieces, Ch. 7 Scheduling: Introduction", "authors": "R. H. Arpaci-Dusseau, "
         "A. C. Arpaci-Dusseau", "year": 2018, "venue": "Arpaci-Dusseau Books (v1.10)", "url":
         "https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched.pdf", "section": "7.3-7.7",
         "verified": "PDF fetched and text extracted 2026-10-03; FIFO 110 s / SJF 50 s example and definitions confirmed."},
        ["turnaround = completion - arrival", "waiting = turnaround - burst (all arrive at 0)",
         "response = first run - arrival", "RR: run min(quantum, remaining), then go to queue tail"],
        [
            {"label": "OSTEP convoy example A=100, B=10, C=10", "params": {"bursts": ost},
             "expected": {p: schedule(ost, p, 10) for p in ("FCFS", "SJF", "RR")}, "tol": 1e-9,
             "note": "RR uses quantum 10"},
            {"label": "classic P1=24, P2=3, P3=3", "params": {"bursts": sil, "quantum": 4},
             "expected": {p: schedule(sil, p, 4) for p in ("FCFS", "SJF", "RR")}, "tol": 1e-6,
             "note": "avg waiting FCFS 17, SJF 3, RR(q=4) 17/3"},
            {"label": "three equal 5 s jobs, slice 1", "params": {"bursts": [5, 5, 5], "quantum": 1},
             "expected": {p: schedule([5, 5, 5], p, 1) for p in ("SJF", "RR")}, "tol": 1e-9,
             "note": "RR avg response 1 vs SJF 5; RR avg turnaround 14 vs SJF 10"},
        ],
        [
            "Turnaround = completion - arrival; response = first run - arrival",
            "FIFO suffers from the convoy effect when a long job is ahead of short ones",
            "SJF minimises average turnaround/waiting time when all jobs arrive together",
            "Round robin gives each job a time slice in turn: good response time, poor turnaround",
            "The time slice trades responsiveness against context-switch overhead",
            "In the OSTEP example (100, 10, 10) FIFO average turnaround is 110 s and SJF is 50 s",
        ],
        [
            "Waiting time computed as completion time",
            "RR queue order wrong (preempted job placed at the head)",
            "Mixing up response and turnaround time",
            "Claiming RR minimises average turnaround",
            "SJF tie-breaking not stated",
        ],
        [
            q("What is turnaround time?", ["First run - arrival", "Completion - arrival", "Burst length",
              "Quantum length"], "B", 0),
            q("What is the convoy effect?", ["Short jobs stuck behind a long job in FIFO", "Jobs running in parallel",
              "Too many context switches", "Starvation of long jobs in RR"], "A", 1),
            q("Which policy minimises average turnaround when all jobs arrive together?", ["FIFO", "SJF", "RR",
              "Random"], "B", 2),
            q("Round robin is good for:", ["Turnaround time", "Response time", "Throughput only", "Nothing"], "B", 3),
            q("In OSTEP's example with jobs of 100, 10 and 10 s, FIFO's average turnaround is:",
              ["50 s", "110 s", "120 s", "40 s"], "B", 5),
        ],
        invariants=["sum of bursts == makespan for all policies", "SJF avg waiting <= FCFS avg waiting",
                    "RR avg response <= SJF avg response for equal jobs"],
        benchmark=pv_bench("non-arXiv textbook PDF URL"),
    )

    # pv_page_replacement ------------------------------------------------------------------------
    belady = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]
    pf = {f"{pol}_{n}_frames": page_faults(belady, n, pol) for pol in ("FIFO", "LRU", "OPT") for n in (3, 4)}
    s2 = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2]
    case(
        "pv_page_replacement",
        "https://doi.org/10.1145/363011.363155",
        "Belady, Nelson and Shedler (1969), An Anomaly in Space-Time Characteristics of Certain Programs Running "
        "in a Paging Machine. Step through a page reference string frame by frame for FIFO and LRU replacement, "
        "marking hits and faults. Let the learner edit the reference string and the number of frames. Guide them "
        "to the anomaly: with FIFO, more frames can cause more faults. Check that 1,2,3,4,1,2,5,1,2,3,4,5 gives "
        "9 FIFO faults with 3 frames and 10 with 4, and that LRU never gets worse with more frames.",
        "Computer engineering junior taking an operating systems course.",
        """
Source: L. A. Belady, R. A. Nelson and G. S. Shedler, "An Anomaly in Space-Time Characteristics of
Certain Programs Running in a Paging Machine", Communications of the ACM 12(6) (1969) 349-353.
Paraphrased (ACM page returns 403 to scripts); the reference string below is the standard
illustration of their result.

In a paging machine, a program's pages are brought into a fixed number of main-memory page frames on
demand. When a referenced page is absent (a page fault) and all frames are full, a replacement
algorithm chooses a page to evict. Intuition says that giving a program more frames can never
increase its number of page faults. The authors showed that this is false for the
first-in-first-out (FIFO) rule: for certain reference strings, FIFO produces more faults with more
memory. This is now called Belady's anomaly.

Example: the reference string 1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5 causes 9 faults with 3 frames under
FIFO but 10 faults with 4 frames.

Least-recently-used (LRU) replacement evicts the page whose last use is furthest in the past. LRU and
the optimal rule (evict the page whose next use is furthest in the future) are stack algorithms: the
set of pages held with n frames is always contained in the set held with n + 1 frames, so they
cannot exhibit the anomaly.
""",
        {"title": "An Anomaly in Space-Time Characteristics of Certain Programs Running in a Paging Machine",
         "authors": "L. A. Belady, R. A. Nelson, G. S. Shedler", "year": 1969, "venue": "Commun. ACM 12(6):349-353",
         "url": "https://doi.org/10.1145/363011.363155", "section": "the FIFO anomaly",
         "verified": "DOI returns 403 to scripts (2026-10-03); counts recomputed here (FIFO 9/10 confirmed)."},
        ["FIFO: evict the oldest loaded page", "LRU: evict the least recently used page",
         "OPT: evict the page used furthest in the future", "faults = misses over the string"],
        [
            {"label": "Belady string", "params": {"refs": belady, "frames": [3, 4]},
             "expected": {k: {"faults": v[0], "hit_fault_trace": v[1]} for k, v in pf.items()}, "tol": 0},
            {"label": "textbook string 7,0,1,2,0,3,0,4,2,3,0,3,2", "params": {"refs": s2, "frames": 3},
             "expected": {pol: page_faults(s2, 3, pol)[0] for pol in ("FIFO", "LRU", "OPT")}, "tol": 0},
            {"label": "frames >= distinct pages", "params": {"refs": belady, "frames": 5},
             "expected": {pol: page_faults(belady, 5, pol)[0] for pol in ("FIFO", "LRU")}, "tol": 0,
             "note": "only compulsory faults (5)"},
        ],
        [
            "A page fault occurs when a referenced page is not in memory; replacement chooses a victim when frames are full",
            "FIFO evicts the page that has been in memory longest; LRU evicts the least recently used page",
            "Belady's anomaly: with FIFO, more frames can produce more page faults",
            "1,2,3,4,1,2,5,1,2,3,4,5 gives 9 FIFO faults with 3 frames and 10 with 4",
            "LRU and optimal are stack algorithms and never show the anomaly",
            "Optimal replacement evicts the page whose next use is furthest in the future",
        ],
        [
            "Updating the FIFO queue on a hit (that is LRU behaviour)",
            "Not counting the initial compulsory faults",
            "Claiming LRU can show Belady's anomaly",
            "Claiming more frames always reduce faults for every algorithm",
            "Off-by-one frame count in the display",
        ],
        [
            q("What is Belady's anomaly?", ["LRU is slower than FIFO", "With FIFO, more frames can cause more page faults",
              "Optimal replacement is impossible", "Pages are loaded twice"], "B", 2),
            q("How many FIFO faults does 1,2,3,4,1,2,5,1,2,3,4,5 cause with 4 frames?", ["8", "9", "10", "12"], "C", 3),
            q("Which page does LRU evict?", ["The newest page", "The least recently used page", "A random page",
              "The page used next"], "B", 1),
            q("Why can LRU not show the anomaly?", ["It is a stack algorithm", "It uses fewer frames",
              "It never faults", "It is FIFO in disguise"], "A", 4),
            q("Which algorithm evicts the page whose next use is furthest in the future?", ["FIFO", "LRU", "Optimal",
              "Clock"], "C", 5),
        ],
        invariants=["faults >= number of distinct pages", "LRU/OPT faults non-increasing in frames", "OPT <= LRU, OPT <= FIFO"],
        benchmark=pv_bench("DOI URL (ACM)"),
    )

    # pv_cuckoo_hashing --------------------------------------------------------------------------
    ck1 = cuckoo_insert_all([3, 8, 13, 20, 1])
    ck2 = cuckoo_insert_all([0, 25, 50])
    case(
        "pv_cuckoo_hashing",
        "https://doi.org/10.1016/j.jalgor.2003.12.002",
        "Pagh and Rodler, Cuckoo Hashing. Animate insertions into two small tables with two hash functions: "
        "place a key in its first-table slot, and if it is occupied, kick out the resident key to its slot in "
        "the other table, and repeat. Let the learner type keys and see the eviction chain, lookups that probe "
        "at most two cells, and a failed insertion that needs a rehash.",
        "Computer science sophomore who has implemented a hash table with chaining.",
        """
Source: R. Pagh and F. F. Rodler, "Cuckoo Hashing", Journal of Algorithms 51(2) (2004) 122-144
(conference version: ESA 2001, LNCS 2161). Paraphrased.

Cuckoo hashing is a dictionary with worst-case constant lookup time. It uses two tables T1 and T2,
each of size r, and two hash functions h1 and h2. Every key x is stored either in cell h1(x) of T1 or
in cell h2(x) of T2, never anywhere else. So a lookup inspects at most two cells, and a deletion
simply clears the cell that holds the key.

Insertion follows the behaviour of the cuckoo chick, which pushes other eggs out of the nest. A new
key x is put into T1[h1(x)]. If that cell was occupied, the previous occupant y is kicked out and
moved to its alternative cell T2[h2(y)], which may in turn kick out another key, which goes back to
its cell in T1, and so on. The process ends when a key lands in an empty cell. If it has not ended
after MaxLoop steps, the keys may be on a cycle that can never be resolved; new hash functions are
then chosen and everything is rehashed. The analysis requires each table to be somewhat larger than
the number of keys (r >= (1 + epsilon) n, i.e. total load below 50 percent). Under that condition,
with suitable hash functions, insertion takes expected constant amortized time.

Teaching example used here: r = 5, h1(k) = k mod 5, h2(k) = floor(k/5) mod 5, MaxLoop = 10.
""",
        {"title": "Cuckoo Hashing", "authors": "R. Pagh, F. F. Rodler", "year": 2004, "venue": "J. Algorithms 51(2):122-144",
         "url": "https://doi.org/10.1016/j.jalgor.2003.12.002", "section": "Sec. 2-3 (algorithm, insertion procedure)",
         "verified": "DOI resolves (200) 2026-10-03; algorithm stated from the paper as widely reproduced; toy hash "
                     "functions chosen here."},
        ["h1(k) = k mod 5; h2(k) = floor(k/5) mod 5 (teaching example)", "insert: swap into T1[h1], evicted key goes to "
         "T2[h2], alternate tables", "lookup: check T1[h1(x)] and T2[h2(x)] only", "fail after MaxLoop -> rehash"],
        [
            {"label": "insert 3, 8, 13, 20, 1", "params": {"keys": [3, 8, 13, 20, 1], "r": 5, "max_loop": 10},
             "expected": ck1, "tol": 0},
            {"label": "unresolvable: 0, 25, 50 share both cells", "params": {"keys": [0, 25, 50], "r": 5, "max_loop": 10},
             "expected": {"placed": [e["placed"] for e in ck2["log"]], "needs_rehash": not ck2["log"][-1]["placed"]},
             "tol": 0, "note": "three keys with identical (h1, h2) cannot fit in two cells"},
            {"label": "lookup cost", "params": {"key": 13}, "expected": {"max_cells_probed": 2}, "tol": 0},
        ],
        [
            "Two tables and two hash functions; each key lives in T1[h1(x)] or T2[h2(x)]",
            "Lookup checks at most two cells (worst-case constant time)",
            "Insertion kicks out the occupant, which moves to its alternative cell, possibly causing a chain",
            "If the chain exceeds MaxLoop, new hash functions are chosen and all keys are rehashed",
            "Requires load below 50% (each table larger than the number of keys) for expected constant insertion",
            "The name comes from the cuckoo chick pushing other eggs out of the nest",
        ],
        [
            "Placing the evicted key back into the same table",
            "Lookup scanning more than two cells",
            "Infinite loop instead of a MaxLoop bound and rehash",
            "Losing a key during the eviction chain",
            "Claiming insertion is worst-case constant time (it is lookup that is worst-case constant)",
        ],
        [
            q("How many cells does a cuckoo-hashing lookup inspect at most?", ["1", "2", "log n", "n"], "B", 1),
            q("What happens when a new key's cell is occupied?", ["The new key is dropped", "The occupant is kicked out to its "
              "cell in the other table", "A linked list grows", "The table doubles immediately"], "B", 2),
            q("What is done if the eviction chain is too long?", ["Give up silently", "Rehash with new hash functions",
              "Use linear probing", "Delete a key"], "B", 3),
            q("Where can key x be stored?", ["Anywhere", "Only in T1[h1(x)] or T2[h2(x)]", "In a bucket list",
              "At the end of T2"], "B", 0),
            q("What load condition does the analysis need?", ["Load above 90%", "Each table somewhat larger than the "
              "number of keys (load < 50%)", "Exactly full tables", "No condition"], "B", 4),
        ],
        invariants=["every stored key k is at T1[h1(k)] or T2[h2(k)]", "no key lost or duplicated during evictions"],
        benchmark=pv_bench("DOI URL (Elsevier)"),
    )

    # pv_lorenz --------------------------------------------------------------------------------
    sC = math.sqrt(8 / 3 * 27)
    end_chaos, t_sep = lorenz_run((1.0, 1.0, 1.0), T=40.0, dt=0.001, delta=1e-8)
    end_low, _ = lorenz_run((1.0, 1.0, 1.0), T=40.0, dt=0.001, rho=0.5)
    end_mid, _ = lorenz_run((1.0, 1.0, 1.0), T=60.0, dt=0.001, rho=14.0)
    sC14 = math.sqrt(8 / 3 * 13)
    case(
        "pv_lorenz",
        "https://doi.org/10.1175/1520-0469(1963)020<0130:DNF>2.0.CO;2",
        "Lorenz (1963), Deterministic Nonperiodic Flow. Show sensitive dependence on initial conditions with two "
        "trajectories of the three Lorenz equations that start a tiny distance apart. Let the learner set sigma, "
        "r and b, the initial point and the size of the perturbation; plot both trajectories in 3D or as x(t), "
        "and the distance between them on a log scale. Guide them through r below 1 (everything decays to the "
        "origin), r = 14 (settles to a fixed point) and r = 28 (chaos).",
        "Applied mathematics junior who has studied linear ODE systems and eigenvalues.",
        """
Source: E. N. Lorenz, "Deterministic Nonperiodic Flow", Journal of the Atmospheric Sciences 20 (1963)
130-141. Paraphrased (publisher page not fetched by script).

Lorenz reduced a model of convection in a layer of fluid heated from below to three ordinary
differential equations:

    dX/dt = -sigma X + sigma Y
    dY/dt = -X Z + r X - Y
    dZ/dt = X Y - b Z

X measures the intensity of convective motion, Y the temperature difference between rising and
falling currents, and Z the distortion of the vertical temperature profile. sigma is the Prandtl
number, r the Rayleigh number relative to its critical value, and b a geometric factor. Lorenz used
sigma = 10, b = 8/3 and r = 28 and integrated numerically.

For r < 1 the state of rest (the origin) is stable. For r > 1 there are two steady convection states
C and C' at X = Y = +/- sqrt(b(r - 1)), Z = r - 1, which are stable only below a critical r (about
24.74 for these sigma and b). At r = 28 all three steady states are unstable, and solutions wander
forever between the neighbourhoods of C and C' without settling into a periodic pattern; they are
deterministic yet nonperiodic. Lorenz showed that two states differing by imperceptible amounts can
evolve into considerably different states. He concluded that if the atmosphere behaves this way,
prediction of the sufficiently distant future is impossible by any method unless the present
conditions are known exactly.
""",
        {"title": "Deterministic Nonperiodic Flow", "authors": "E. N. Lorenz", "year": 1963, "venue": "J. Atmos. Sci. "
         "20(2):130-141", "url": "https://doi.org/10.1175/1520-0469(1963)020<0130:DNF>2.0.CO;2",
         "section": "equations (25)-(27), parameters, steady states, conclusions",
         "verified": "DOI returns 403 to scripts (2026-10-03); equations and parameter values stated from the paper "
                     "as widely reproduced; Hopf value computed here."},
        ["dx = sigma (y - x); dy = x (r - z) - y; dz = x y - b z", "C+- = (+-sqrt(b (r-1)), +-sqrt(b (r-1)), r-1)",
         "r_H = sigma (sigma + b + 3)/(sigma - b - 1)", "separation grows ~ exp(lambda t), lambda ~ 0.9"],
        [
            {"label": "chaos, r = 28", "params": {"sigma": 10, "r": 28, "b": "8/3", "x0": [1, 1, 1], "delta_x0": 1e-8,
             "integrator": "RK4 dt=0.001"},
             "expected": {"fixed_points": [[sC, sC, 27.0], [-sC, -sC, 27.0], [0, 0, 0]],
                          "hopf_r": 10 * (10 + 8 / 3 + 3) / (10 - 8 / 3 - 1),
                          "time_separation_exceeds_1": t_sep, "largest_lyapunov_literature": 0.9056,
                          "state_at_t40": list(end_chaos)},
             "tol": 0.15, "note": "fixed points and r_H exact (tol 1e-4); separation time is integrator-dependent: accept "
                                   "roughly 20-35 time units for delta = 1e-8 (ln(1e8)/0.9 ~ 20 plus transient); state "
                                   "at t=40 is NOT reproducible across integrators (that is the point)"},
            {"label": "r = 0.5 (below 1)", "params": {"sigma": 10, "r": 0.5, "b": "8/3", "x0": [1, 1, 1]},
             "expected": {"state_at_t40": list(end_low), "limit": [0, 0, 0]}, "tol": 1e-3},
            {"label": "r = 14 (stable convection)", "params": {"sigma": 10, "r": 14, "b": "8/3", "x0": [1, 1, 1]},
             "expected": {"state_at_t60": list(end_mid), "C_plus": [sC14, sC14, 13.0]}, "tol": 1e-2},
        ],
        [
            "Lorenz equations dX/dt = sigma(Y - X), dY/dt = rX - Y - XZ, dZ/dt = XY - bZ",
            "Lorenz's parameters: sigma = 10, b = 8/3, r = 28",
            "Steady convection states at X = Y = +-sqrt(b(r-1)), Z = r - 1; origin stable for r < 1",
            "At r = 28 solutions are deterministic but nonperiodic and never settle",
            "Two nearly identical initial states diverge to very different states (sensitive dependence)",
            "Hence long-range prediction is impossible unless initial conditions are known exactly",
        ],
        [
            "Forward Euler with large steps presented as the true trajectory",
            "Claiming the system is random or noisy (it is deterministic)",
            "Fixed points with Z = r instead of r - 1",
            "Showing the two trajectories diverge immediately rather than exponentially from a tiny gap",
            "Attributing the 'butterfly effect' talk (1972) or the rounding anecdote to the 1963 paper",
        ],
        [
            q("Which parameter values did Lorenz use?", ["sigma=10, b=8/3, r=28", "sigma=1, b=1, r=1",
              "sigma=28, b=10, r=8/3", "sigma=0, b=0, r=0"], "A", 1),
            q("What happens to two trajectories that start extremely close together at r = 28?",
              ["They stay together forever", "They eventually become very different", "They both go to zero",
               "They become periodic"], "B", 4),
            q("Is the Lorenz system random?", ["Yes, it contains noise", "No, it is deterministic yet nonperiodic",
              "Only for r < 1", "Only for b = 0"], "B", 3),
            q("Where are the steady convection states?", ["X=Y=+-sqrt(b(r-1)), Z=r-1", "X=Y=Z=0 only", "X=Y=r, Z=b",
              "X=sigma, Y=Z=0"], "A", 2),
            q("What did Lorenz conclude about weather prediction?", ["It is easy with more computing",
              "Long-range prediction is impossible unless initial conditions are known exactly",
              "Weather is periodic", "Only temperature can be predicted"], "B", 5),
        ],
        invariants=["origin attracts for r < 1", "trajectories bounded", "log separation grows ~linearly until saturation"],
        benchmark=pv_bench("DOI URL containing <, > and ; characters"),
    )

"""Build the DEV-ONLY evaluation cases: eval/cases/<slug>/{case.json, excerpt.txt, reference.json}.

Run:  python eval/build_cases.py            (writes/overwrites all case folders)
      python eval/build_cases.py --check    (recomputes and prints the reference values only)

Everything that grades a generated page lives here so it is reproducible and reviewable:
  * case.json      -> the 3 required string fields, exactly what agent.py receives
  * excerpt.txt    -> short faithful paraphrase/quotation of the source (<= 300 words),
                      used for the "excerpt available" condition (paper2play/source.py reads a
                      sibling excerpt.txt next to case.json)
  * reference.json -> independently computed expected values (formulas below, stdlib math only),
                      must-mention facts, common errors and a 5-question quiz.
reference.json is ground truth for grading ONLY. scripts/eval.py never copies it into a run
directory and the agent must never read it.
"""
from __future__ import annotations

import argparse
import cmath
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "cases"
DEV_NOTE = ("DEV-ONLY ground truth for grading generated pages. Never shipped with, shown to, or read by "
            "the agent.")


def r6(x):
    """Round floats (recursively) to 6 significant digits for readable JSON."""
    if isinstance(x, float):
        if x == 0 or not math.isfinite(x):
            return x
        return float(f"{x:.6g}")
    if isinstance(x, complex):
        return {"re": r6(x.real), "im": r6(x.imag)}
    if isinstance(x, (list, tuple)):
        return [r6(v) for v in x]
    if isinstance(x, dict):
        return {k: r6(v) for k, v in x.items()}
    return x


def q(question, options, answer, fact):
    """One multiple-choice question. `fact` = index into must_mention it tests."""
    assert answer in "ABCD" and len(options) == 4
    return {"q": question, "options": dict(zip("ABCD", options)), "answer": answer, "fact": fact}


# ----------------------------------------------------------------------------------------------
# Reference computations (independent of the agent; plain formulas)
# ----------------------------------------------------------------------------------------------

def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def transpose(a):
    return [list(r) for r in zip(*a)]


def softmax(row):
    m = max(row)
    e = [math.exp(v - m) for v in row]
    s = sum(e)
    return [v / s for v in e]


def attention(Q, K, V, scale=True):
    dk = len(K[0])
    S = matmul(Q, transpose(K))
    if scale:
        S = [[v / math.sqrt(dk) for v in row] for row in S]
    W = [softmax(row) for row in S]
    return {"scores": S, "weights": W, "output": matmul(W, V), "row_sums": [sum(r) for r in W]}


def entropy_bits(p):
    # 0 log 0 := 0 (limit), Shannon 1948 sec. 6
    return -sum(x * math.log2(x) for x in p if x > 0)


def sir_final_size(R0, s0, i0):
    """Normalised SIR (N=1, r0=0): ln(s0/s_inf) = R0 (1 - s_inf) when i_inf = 0.
    Solved by bisection on f(s)=ln(s0/s) - R0(1-s) over (0, min(s0, 1/R0)]."""
    # f -> +inf as s -> 0, f(min(s0, 1/R0)) < 0 and f is decreasing below 1/R0, so the physical root
    # is unique in (0, min(s0, 1/R0)).
    f = lambda s: math.log(s0 / s) - R0 * (1 - s)
    lo, hi = 1e-12, min(s0, 1.0 / R0)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    s_inf = 0.5 * (lo + hi)
    return s_inf


def sir_simulate(beta, gamma, s0, i0, days=200, dt=0.01):
    s, i, r = s0, i0, 1 - s0 - i0
    peak_i, peak_t = i, 0.0
    t = 0.0
    def d(s, i):
        return -beta * s * i, beta * s * i - gamma * i
    while t < days - 1e-12:
        k1 = d(s, i)
        k2 = d(s + dt / 2 * k1[0], i + dt / 2 * k1[1])
        k3 = d(s + dt / 2 * k2[0], i + dt / 2 * k2[1])
        k4 = d(s + dt * k3[0], i + dt * k3[1])
        s += dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        i += dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        t += dt
        if i > peak_i:
            peak_i, peak_t = i, t
    return {"s_end": s, "i_end": i, "r_end": 1 - s - i, "peak_i": peak_i, "peak_day": peak_t}


def lv_simulate(a, b, c, d, x0, y0, T, dt=0.001):
    """dx/dt = a x - b x y ; dy/dt = d x y - c y  (RK4). Returns time-averages and conserved V drift."""
    def f(x, y):
        return a * x - b * x * y, d * x * y - c * y
    V = lambda x, y: d * x - c * math.log(x) + b * y - a * math.log(y)
    x, y = x0, y0
    V0 = V(x, y)
    n = int(round(T / dt))
    sx = sy = 0.0
    xmax = ymax = 0.0
    xmin = ymin = float("inf")
    # find period via upward crossings of x through c/d
    crossings = []
    prev = x - c / d
    for k in range(n):
        k1 = f(x, y)
        k2 = f(x + dt / 2 * k1[0], y + dt / 2 * k1[1])
        k3 = f(x + dt / 2 * k2[0], y + dt / 2 * k2[1])
        k4 = f(x + dt * k3[0], y + dt * k3[1])
        x += dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        y += dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        cur = x - c / d
        if prev < 0 <= cur:
            crossings.append((k + 1) * dt)
        prev = cur
        xmax, ymax, xmin, ymin = max(xmax, x), max(ymax, y), min(xmin, x), min(ymin, y)
    # time averages over whole number of periods
    period = (crossings[-1] - crossings[0]) / (len(crossings) - 1) if len(crossings) > 1 else float("nan")
    # recompute averages over [crossings[0], crossings[-1]]
    x, y = x0, y0
    t0i, t1i = int(round(crossings[0] / dt)), int(round(crossings[-1] / dt))
    for k in range(t1i):
        if k >= t0i:
            sx += x * dt
            sy += y * dt
        k1 = f(x, y)
        k2 = f(x + dt / 2 * k1[0], y + dt / 2 * k1[1])
        k3 = f(x + dt / 2 * k2[0], y + dt / 2 * k2[1])
        k4 = f(x + dt * k3[0], y + dt * k3[1])
        x += dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        y += dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    span = (t1i - t0i) * dt
    return {"mean_prey": sx / span, "mean_pred": sy / span, "period": period,
            "prey_range": [xmin, xmax], "pred_range": [ymin, ymax], "V0": V0}


def Phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes(S, K, r, sigma, T):
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    C = S * Phi(d1) - K * math.exp(-r * T) * Phi(d2)
    P = K * math.exp(-r * T) * Phi(-d2) - S * Phi(-d1)
    vega = S * math.sqrt(T) * math.exp(-d1 ** 2 / 2) / math.sqrt(2 * math.pi)
    return {"d1": d1, "d2": d2, "call": C, "put": P, "delta_call": Phi(d1), "vega": vega,
            "parity_lhs_C_minus_P": C - P, "parity_rhs_S_minus_PV_K": S - K * math.exp(-r * T)}


def alias(f, fs):
    return abs(f - fs * round(f / fs))


def adam_trace(grads, b1=0.9, b2=0.999, alpha=0.001, eps=1e-8):
    m = v = 0.0
    out = []
    for t, g in enumerate(grads, start=1):
        m = b1 * m + (1 - b1) * g
        v = b2 * v + (1 - b2) * g * g
        mh = m / (1 - b1 ** t)
        vh = v / (1 - b2 ** t)
        out.append({"t": t, "g": g, "m": m, "v": v, "m_hat": mh, "v_hat": vh,
                    "step_corrected": alpha * mh / (math.sqrt(vh) + eps),
                    "step_uncorrected": alpha * m / (math.sqrt(v) + eps)})
    return out


def ddpm_alpha_bar(t, T=1000, b1=1e-4, bT=0.02):
    ab = 1.0
    for s in range(1, t + 1):
        beta = b1 + (bT - b1) * (s - 1) / (T - 1)
        ab *= 1 - beta
    return ab


def schelling_sim(seed, n=20, empty=0.1, thr=0.3, max_rounds=200):
    rng = random.Random(seed)
    cells = n * n
    n_empty = int(round(empty * cells))
    n_a = (cells - n_empty) // 2
    n_b = cells - n_empty - n_a
    vals = [0] * n_empty + [1] * n_a + [2] * n_b
    rng.shuffle(vals)
    g = [vals[i * n:(i + 1) * n] for i in range(n)]

    def like_frac(i, j):
        me = g[i][j]
        same = occ = 0
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == dj == 0:
                    continue
                v = g[(i + di) % n][(j + dj) % n]
                if v:
                    occ += 1
                    same += v == me
        return same / occ if occ else 1.0

    def mean_like():
        fr = [like_frac(i, j) for i in range(n) for j in range(n) if g[i][j]]
        return sum(fr) / len(fr)

    start = mean_like()
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        unhappy = [(i, j) for i in range(n) for j in range(n) if g[i][j] and like_frac(i, j) < thr]
        if not unhappy:
            rounds -= 1
            break
        rng.shuffle(unhappy)
        for (i, j) in unhappy:
            empties = [(a, b) for a in range(n) for b in range(n) if not g[a][b]]
            a, b = empties[rng.randrange(len(empties))]
            g[a][b], g[i][j] = g[i][j], 0
    unhappy_end = sum(1 for i in range(n) for j in range(n) if g[i][j] and like_frac(i, j) < thr)
    return start, mean_like(), rounds, unhappy_end


def pn_junction(Na, Nd, ni, er, kT=0.0259, q_e=1.6e-19, eps0=8.854e-14):
    phi_p = kT * math.log(Na / ni)
    phi_n = kT * math.log(Nd / ni)
    phi_i = phi_p + phi_n
    eps = er * eps0
    xd = math.sqrt(2 * eps * phi_i / q_e * (Na + Nd) / (Na * Nd))
    xn = xd * Na / (Na + Nd)
    xp = xd * Nd / (Na + Nd)
    Emax = q_e * Nd * xn / eps
    return {"phi_p_V": phi_p, "phi_n_V": phi_n, "V_bi_V": phi_i, "x_d_cm": xd, "x_n_cm": xn,
            "x_p_cm": xp, "E_max_V_per_cm": Emax, "neutrality_Na_xp": Na * xp, "neutrality_Nd_xn": Nd * xn}


def dbr_scicode(lam, lam_b, n1, n2, N):
    """SciCode problem 39 formulation (Yeh's periodic-stack result), computed independently."""
    phi = math.pi * lam_b / (2 * lam)
    A = (n1 + n2) ** 2 / (4 * n1 * n2) * cmath.exp(-2j * phi) - (n1 - n2) ** 2 / (4 * n1 * n2)
    B = (n2 ** 2 - n1 ** 2) / (4 * n1 * n2) * (1 - cmath.exp(2j * phi))
    C = B.conjugate()
    D = A.conjugate()
    half = ((A + D) / 2).real
    if abs(half) <= 1:
        th = math.acos(half)
        ratio = abs(math.sin(th) / math.sin(N * th)) if abs(math.sin(N * th)) > 1e-300 else 0.0
    else:
        alpha = math.acosh(abs(half))
        ratio = abs(math.sinh(alpha) / math.sinh(N * alpha))
    R = abs(C) ** 2 / (abs(C) ** 2 + ratio ** 2)
    return {"phi": phi, "A": A, "B": B, "half_trace": half, "R": R}


def dbr_peak_closed_form(n1, n2, N, n_in=None, n_out=None):
    """Quarter-wave stack at the Bragg wavelength embedded in n1 on both sides: R = tanh^2(N ln(n1/n2))."""
    return math.tanh(N * math.log(n1 / n2)) ** 2


def sigma_delta(x, N):
    """First-order Delta-Sigma: v[n] = v[n-1] + x - y[n-1];  y[n] = +1 if v[n] >= 0 else -1; v[-1]=y[-1]=0."""
    v, y = 0.0, 0.0
    bits = []
    for _ in range(N):
        v = v + x - y
        y = 1.0 if v >= 0 else -1.0
        bits.append(int(y))
    return bits, sum(bits) / N


# ----------------------------------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------------------------------

CASES = []


def case(slug, source_url, focus, audience, excerpt, source, formulas, settings, must, errors, quiz,
         invariants=None, benchmark=None):
    CASES.append(dict(slug=slug, case={"source_url": source_url, "focus": focus, "audience": audience},
                      excerpt=excerpt.strip() + "\n", source=source, formulas=formulas, settings=settings,
                      invariants=invariants or [], must_mention=must, common_errors=errors, quiz=quiz,
                      benchmark=benchmark))


# 1. Attention ---------------------------------------------------------------------------------
Q1 = [[1, 0, 1], [0, 1, 0]]
K1 = [[1, 0, 1], [0, 1, 0], [1, 1, 0]]
V1 = [[1, 0], [0, 1], [1, 1]]
Kid = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
Vid = [[1, 0], [0, 1], [1, 1]]
case(
    "attention",
    "https://arxiv.org/html/1706.03762v7",
    "Attention Is All You Need, Section 3.2.1. Explain scaled dot-product attention using small, editable "
    "Q, K, and V matrices. Show similarity scores, normalized attention weights, and the output. Let the "
    "learner edit values and switch scaling on/off. Guide them through equal scores and a dominant score. "
    "Check that each row of weights sums to one and that the output equals the weighted sum of V. Do not "
    "train a Transformer.",
    "Second-year engineering undergraduate who is comfortable with matrix multiplication but has never seen "
    "attention or neural networks.",
    """
Source: Vaswani et al., "Attention Is All You Need" (NeurIPS 2017), Section 3.2.1 "Scaled Dot-Product
Attention". Paraphrased excerpt, checked against https://arxiv.org/html/1706.03762v7.

The input consists of queries and keys of dimension d_k and values of dimension d_v. For each query
the dot products with all keys are computed, each is divided by sqrt(d_k), and a softmax is applied to
obtain the weights on the values. In practice the queries, keys and values are packed into matrices
Q, K and V and the output is computed as

    Attention(Q, K, V) = softmax(Q K^T / sqrt(d_k)) V        (Eq. 1)

The two most common attention functions are additive attention and dot-product (multiplicative)
attention. Dot-product attention is identical to this algorithm except for the scaling factor
1/sqrt(d_k). It is much faster and more space-efficient in practice because it can use highly
optimised matrix multiplication code. For small d_k both mechanisms perform similarly, but additive
attention outperforms unscaled dot-product attention for larger d_k. The authors suspect that for
large d_k the dot products grow large in magnitude, pushing the softmax into regions where it has
extremely small gradients; to counteract this they scale the dot products by 1/sqrt(d_k).

Footnote 4: if the components of q and k are independent random variables with mean 0 and
variance 1, then their dot product q.k = sum_i q_i k_i has mean 0 and variance d_k.
""",
    {"title": "Attention Is All You Need", "authors": "Vaswani, Shazeer, Parmar, Uszkoreit, Jones, Gomez, "
     "Kaiser, Polosukhin", "year": 2017, "venue": "NeurIPS 2017 (arXiv:1706.03762)",
     "url": "https://arxiv.org/html/1706.03762v7", "section": "3.2.1, Eq. (1), footnote 4",
     "verified": "Fetched arXiv HTML v7 on 2026-10-03; equation and scaling rationale confirmed."},
    ["S = Q K^T (optionally / sqrt(d_k))", "W = row-wise softmax(S); each row sums to 1",
     "Output = W V (row i = sum_j W_ij V_j)"],
    [
        {"label": "default (scaled)", "params": {"Q": Q1, "K": K1, "V": V1, "scale": True},
         "expected": attention(Q1, K1, V1, True), "tol": 1e-3},
        {"label": "same, scaling off", "params": {"Q": Q1, "K": K1, "V": V1, "scale": False},
         "expected": attention(Q1, K1, V1, False), "tol": 1e-3},
        {"label": "equal scores (q = 0)", "params": {"Q": [[0, 0, 0]], "K": K1, "V": V1, "scale": True},
         "expected": attention([[0, 0, 0]], K1, V1, True), "tol": 1e-3,
         "note": "uniform weights 1/3; output = mean of V rows = [2/3, 2/3]"},
        {"label": "dominant score", "params": {"Q": [[10, 0, 0]], "K": Kid, "V": Vid, "scale": True},
         "expected": attention([[10, 0, 0]], Kid, Vid, True), "tol": 1e-3,
         "note": "first key dominates; output ~ V row 1"},
    ],
    [
        "Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V (Eq. 1 of Section 3.2.1)",
        "Scores are dot products between each query and each key",
        "Softmax is applied row-wise, so each row of weights is non-negative and sums to 1",
        "Each output row is a weighted average of the rows of V",
        "Scaling by 1/sqrt(d_k) counteracts large dot products (variance d_k) that push softmax into "
        "small-gradient regions",
        "Equal scores give uniform weights; one dominant score makes the weights nearly one-hot",
    ],
    [
        "Softmax applied column-wise or over the whole matrix instead of per query row",
        "Dividing by d_k instead of sqrt(d_k), or scaling the weights instead of the scores",
        "Multiplying by K instead of K^T (dimension mismatch silently hidden)",
        "Claiming scaling changes which key gets the largest weight (it does not change the argmax; it "
        "changes how peaked the distribution is)",
        "Training or learning projections (out of scope: 'Do not train a Transformer')",
    ],
    [
        q("Which operation turns similarity scores into attention weights?",
          ["Row-wise softmax", "Column-wise normalisation by the maximum", "A sigmoid on each score",
           "Division by the number of keys"], "A", 2),
        q("In scaled dot-product attention, what are the dot products divided by?",
          ["d_k", "sqrt(d_k)", "The number of queries", "The norm of V"], "B", 0),
        q("If every score in a row is equal, the attention weights in that row are:",
          ["All zero", "One-hot on the first key", "Uniform (1/n each)", "Undefined"], "C", 5),
        q("How is one row of the output computed?",
          ["As the largest row of V", "As a weighted sum of the rows of V using that row's weights",
           "As Q times K", "As the sum of the scores"], "B", 3),
        q("Why does the paper scale the scores?",
          ["To make the weights sum to one", "Because large dot products push softmax into regions with "
           "tiny gradients", "To make attention additive", "To reduce memory use"], "B", 4),
    ],
    invariants=["every row of weights sums to 1 (|sum-1|<1e-6)", "0 <= weights <= 1",
                "output == weights @ V", "argmax of each weight row is unchanged by toggling scaling"],
)

# 2. Entropy -----------------------------------------------------------------------------------
case(
    "entropy",
    "https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf",
    "A Mathematical Theory of Communication, Section 6. Explain discrete entropy using a small probability "
    "distribution. Let the learner change the distribution and the number of outcomes. Show probabilities, "
    "individual contributions, and total entropy in bits. Compare a certain outcome with equally likely "
    "outcomes. Check that certainty gives zero bits and four equally likely outcomes give two bits; handle "
    "zero probabilities correctly.",
    "First-year engineering undergraduate who knows logarithms and basic probability but no information "
    "theory.",
    """
Source: C. E. Shannon, "A Mathematical Theory of Communication", Bell System Technical Journal 27
(1948), Section 6 "Choice, Uncertainty and Entropy". Paraphrased excerpt, checked against
https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf (pp. 10-11).

Suppose a set of possible events has known probabilities p1, p2, ..., pn. Shannon asks for a measure
H(p1, ..., pn) of how much "choice" is involved, or how uncertain we are of the outcome, and requires:
1. H is continuous in the pi.
2. If all pi are equal (pi = 1/n), H increases monotonically with n: equally likely events offer more
   choice when there are more of them.
3. If a choice is broken into two successive choices, the original H is the weighted sum of the
   individual H values, e.g. H(1/2, 1/3, 1/6) = H(1/2, 1/2) + (1/2) H(2/3, 1/3).

Theorem 2: the only H satisfying these three assumptions has the form H = -K sum_i pi log pi, where K
is a positive constant that merely fixes the unit of measure. Shannon calls H = -sum pi log pi the
entropy of the set of probabilities. With base-2 logarithms the unit is bits. For two possibilities
with probabilities p and q = 1 - p, H = -(p log p + q log q) (Fig. 7).

Properties stated in the section: H = 0 if and only if all the pi but one are zero (we are certain
of the outcome); otherwise H is positive. For a given n, H is a maximum, equal to log n, when all the
pi are equal to 1/n.
""",
    {"title": "A Mathematical Theory of Communication", "authors": "C. E. Shannon", "year": 1948,
     "venue": "Bell System Technical Journal 27:379-423, 623-656", "url":
     "https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf",
     "section": "Section 6, Theorem 2, Fig. 7",
     "verified": "PDF fetched 2026-10-03; Section 6 text extracted (pp. 10-11) and properties confirmed."},
    ["H = -sum_i p_i log2 p_i (bits), with 0 log 0 := 0", "contribution_i = -p_i log2 p_i",
     "H_max = log2 n for uniform p"],
    [
        {"label": "certain outcome", "params": {"p": [1, 0, 0, 0]}, "expected": {"H_bits": 0.0}, "tol": 1e-9},
        {"label": "four equally likely", "params": {"p": [0.25] * 4}, "expected": {"H_bits": 2.0,
         "contributions": [0.5] * 4}, "tol": 1e-9},
        {"label": "[0.5, 0.25, 0.25]", "params": {"p": [0.5, 0.25, 0.25]},
         "expected": {"H_bits": entropy_bits([0.5, 0.25, 0.25]), "contributions": [0.5, 0.5, 0.5]}, "tol": 1e-6},
        {"label": "[0.7, 0.2, 0.1]", "params": {"p": [0.7, 0.2, 0.1]},
         "expected": {"H_bits": entropy_bits([0.7, 0.2, 0.1]),
                      "contributions": [-x * math.log2(x) for x in [0.7, 0.2, 0.1]]}, "tol": 1e-4},
        {"label": "zero entry", "params": {"p": [0.5, 0.5, 0]}, "expected": {"H_bits": 1.0}, "tol": 1e-9,
         "note": "must not produce NaN from 0*log 0"},
        {"label": "Shannon grouping example", "params": {"p": [0.5, 1 / 3, 1 / 6]},
         "expected": {"H_bits": entropy_bits([0.5, 1 / 3, 1 / 6]),
                      "grouping_rhs": 1 + 0.5 * entropy_bits([2 / 3, 1 / 3])}, "tol": 1e-6},
    ],
    [
        "H = -sum p_i log p_i (Theorem 2); with log base 2 the unit is bits",
        "A certain outcome (one p_i = 1) gives H = 0",
        "n equally likely outcomes give the maximum H = log2 n (four outcomes: 2 bits)",
        "Zero-probability outcomes contribute 0 (limit of p log p as p -> 0)",
        "Probabilities must be non-negative and sum to 1",
        "H(1/2,1/3,1/6) = H(1/2,1/2) + 1/2 H(2/3,1/3) (grouping property)",
    ],
    [
        "NaN or -Infinity when a probability is zero",
        "Using natural log but labelling the result 'bits'",
        "Not renormalising after the learner edits one probability (sum != 1)",
        "Claiming entropy can be negative for discrete distributions",
        "Showing contributions p log p with the wrong sign",
    ],
    [
        q("What is the entropy of a distribution where one outcome is certain?",
          ["1 bit", "0 bits", "log2 n bits", "It is undefined"], "B", 1),
        q("Four equally likely outcomes have entropy:", ["1 bit", "4 bits", "2 bits", "0.25 bits"], "C", 2),
        q("How should an outcome with probability 0 be handled?",
          ["It contributes 0 to H", "It makes H infinite", "It must be removed and the page restarted",
           "It contributes -1 bit"], "A", 3),
        q("Which formula defines entropy in bits?",
          ["H = sum p_i^2", "H = -sum p_i log2 p_i", "H = max p_i", "H = log2 (sum p_i)"], "B", 0),
        q("For a fixed number of outcomes n, when is entropy largest?",
          ["When one outcome is certain", "When all outcomes are equally likely",
           "When probabilities are sorted", "It does not depend on the distribution"], "B", 2),
    ],
    invariants=["sum p == 1", "0 <= H <= log2 n", "H == sum of contributions", "no NaN for zero entries"],
)

# 3. SIR -----------------------------------------------------------------------------------------
sir_settings = []
for R0, label in [(3.0, "R0 = 3"), (1.5, "R0 = 1.5"), (0.8, "R0 = 0.8 (below threshold)")]:
    gamma = 0.1
    beta = R0 * gamma
    s0, i0 = 0.999, 0.001
    s_inf = sir_final_size(R0, s0, i0)
    sim = sir_simulate(beta, gamma, s0, i0, days=400)
    i_max_formula = i0 + s0 - (1 + math.log(R0 * s0)) / R0 if R0 * s0 > 1 else i0
    sir_settings.append({
        "label": label,
        "params": {"beta_per_day": beta, "gamma_per_day": gamma, "S0": s0, "I0": i0, "R_init": 0, "N": 1},
        "expected": {"R0": R0, "threshold_S": 1 / R0, "final_susceptible": s_inf,
                     "final_size_ever_infected": 1 - s_inf, "peak_infected_fraction": i_max_formula,
                     "peak_day_rk4": sim["peak_day"] if R0 * s0 > 1 else 0.0,
                     "rk4_check_final_susceptible_400d": sim["s_end"]},
        "tol": 0.01,
        "note": "final size from ln(S0/S_inf) = R0 (1 - S_inf) [N=1, R(0)=0]; peak I from "
                "I_max = I0 + S0 - (1 + ln(R0 S0))/R0; peak day is integration-dependent (+-1 day)",
    })
case(
    "sir",
    "https://royalsocietypublishing.org/doi/10.1098/rspa.1927.0118",
    "Kermack and McKendrick (1927), A Contribution to the Mathematical Theory of Epidemics, the special case "
    "with constant infection and removal rates. Explain the SIR threshold mechanism with small inputs "
    "(population fractions, infection rate, removal rate). Show S, I and R over time and the basic "
    "reproduction ratio. Let the learner change the rates and the initial susceptible fraction. Guide "
    "them through a case below the threshold and one well above it. Check that S + I + R stays constant, "
    "that no epidemic grows when S0 times beta/gamma is at most 1, and that the epidemic ends before all "
    "susceptibles are infected.",
    "Mechanical engineering sophomore who has taken one course on ordinary differential equations.",
    """
Source: W. O. Kermack and A. G. McKendrick, "A Contribution to the Mathematical Theory of Epidemics",
Proc. Royal Society of London A 115 (1927) 700-721. Quoted from the public-domain original
(text checked against a scanned copy, https://math.unm.edu/~sulsky/mathcamp/KermackMcKendrick1927.pdf):

"It will be shown in the sequel that with these reservations, the course of an epidemic is not
necessarily terminated by the exhaustion of the susceptible members of the community. It will appear
that for each particular set of infectivity, recovery and death rates, there exists a critical or
threshold density of population. If the actual population density be equal to (or below) this
threshold value the introduction of one (or more) infected person does not give rise to an epidemic,
whereas if the population be only slightly more dense a small epidemic occurs."

Paraphrase of the special case with constant rates: x = density of susceptibles, y = density of
infected (ill), z = density removed (recovered or dead), kappa = infectivity rate, l = removal rate:
    dx/dt = -kappa x y,   dy/dt = kappa x y - l y,   dz/dt = l y,   x + y + z = N (constant).
The threshold density is l/kappa: y can only grow while x > l/kappa.

From the summary: "No epidemic can occur if the population density is below this threshold value"
and "An epidemic, in general, comes to an end, before the susceptible population has been exhausted."
""",
    {"title": "A Contribution to the Mathematical Theory of Epidemics", "authors": "W. O. Kermack, "
     "A. G. McKendrick", "year": 1927, "venue": "Proc. R. Soc. Lond. A 115(772):700-721",
     "url": "https://royalsocietypublishing.org/doi/10.1098/rspa.1927.0118",
     "section": "Introduction; special case with constant rates; Summary points 2-4",
     "verified": "Publisher page returns 403 to scripts; quotation verified against scanned PDF "
                 "(math.unm.edu mirror) on 2026-10-03."},
    ["dS/dt = -beta S I; dI/dt = beta S I - gamma I; dR/dt = gamma I (fractions, N = 1)",
     "R0 = beta/gamma; I grows iff R0 * S > 1 (threshold S = gamma/beta = l/kappa)",
     "final size: ln(S0/S_inf) = R0 (1 - S_inf) when R(0) = 0",
     "peak I: I_max = I0 + S0 - (1 + ln(R0 S0))/R0"],
    sir_settings,
    [
        "Model: susceptible, infected, removed compartments with dS/dt = -beta S I, dI/dt = beta S I - gamma I, "
        "dR/dt = gamma I",
        "Threshold: infections grow only while S > gamma/beta (Kermack-McKendrick threshold density l/kappa)",
        "R0 = beta/gamma (basic reproduction ratio) for a fully susceptible population",
        "The epidemic ends before all susceptibles are infected (S_inf > 0)",
        "S + I + R is constant (closed population)",
        "Infected numbers peak exactly when S falls to the threshold gamma/beta",
    ],
    [
        "Claiming the epidemic stops because susceptibles run out",
        "Peak of I not at S = gamma/beta",
        "Treating R0 = beta/gamma as independent of S0 when stating the growth condition (it is R0 * S0 > 1)",
        "Euler integration with large steps producing negative S or I, or S + I + R drifting",
        "Mixing counts and fractions (beta S I with S in people needs beta/N)",
    ],
    [
        q("According to Kermack and McKendrick, what typically ends an epidemic?",
          ["Every susceptible person has been infected", "The number of susceptibles drops below a threshold, "
           "so infections decline while susceptibles remain", "The removal rate becomes zero",
           "The population grows"], "B", 3),
        q("In the SIR model, when does the number of infected people increase?",
          ["Whenever beta > 0", "Only while S > gamma/beta", "Only while R > S", "Only after the peak"], "B", 1),
        q("Which quantity stays constant in the closed SIR model?",
          ["S", "I", "S + I + R", "beta * S"], "C", 4),
        q("What is the basic reproduction ratio R0 in terms of the rates?",
          ["gamma/beta", "beta * gamma", "beta/gamma", "beta - gamma"], "C", 2),
        q("At the moment the infected curve peaks, the susceptible fraction equals:",
          ["0", "gamma/beta", "1/2", "S0"], "B", 5),
    ],
    invariants=["S+I+R == 1 within 1e-6", "S non-increasing", "R non-decreasing", "dI/dt>0 iff S > gamma/beta"],
)

# 4. Michaelis-Menten -----------------------------------------------------------------------------
mm = lambda S, Vmax, Km: Vmax * S / (Km + S)
case(
    "michaelis_menten",
    "https://pubs.acs.org/doi/10.1021/bi201284u",
    "Michaelis and Menten (1913), Die Kinetik der Invertinwirkung (English translation by Johnson and Goody, "
    "2011). Explain how the initial reaction rate depends on substrate concentration through a rapidly "
    "formed enzyme-substrate complex. Let the learner change substrate concentration, the maximum rate and "
    "the dissociation constant. Show the rate curve, the fraction of enzyme bound, and the substrate value "
    "giving half the maximum rate. Guide them through low substrate (nearly linear) and saturating "
    "substrate. Check that the rate equals half of Vmax when S equals the constant and never exceeds Vmax.",
    "Chemical engineering undergraduate who knows reaction rates and units but has no enzymology background.",
    """
Source: L. Michaelis and M. L. Menten, "Die Kinetik der Invertinwirkung", Biochemische Zeitschrift 49
(1913) 333-369; English translation and commentary: K. A. Johnson and R. S. Goody, "The Original
Michaelis Constant: Translation of the 1913 Michaelis-Menten Paper", Biochemistry 50 (2011) 8264-8269
(https://pubs.acs.org/doi/10.1021/bi201284u; open copy at PMC3381512). Paraphrased excerpt, checked
against the PMC copy on 2026-10-03.

Michaelis and Menten studied the enzyme invertase hydrolysing sucrose. They postulated that the enzyme
and sucrose rapidly form a compound in equilibrium, with a dissociation constant (the original
"Michaelis constant", here written K_S), and that the rate of reaction is proportional to the
concentration of this enzyme-sucrose compound. With total enzyme E0 and a rate constant c, the
maximum rate is C = c * E0, and the initial rate follows

    v = C * S / (S + K_S)

(the paper's full expression also includes product-inhibition terms for fructose and glucose,
v = C*S / (S + K_S (1 + F/K_F + G/K_G)), which the commentary notes is the modern form of competitive
inhibition). At low S the rate is nearly proportional to S; at high S the enzyme is saturated and the
rate approaches C. When S = K_S the rate is half its maximum. Michaelis and Menten measured progress
curves by optical rotation and showed their fitted constant stayed consistent across sugar
concentrations, supporting the postulated mechanism.
""",
    {"title": "Die Kinetik der Invertinwirkung (translation: The Original Michaelis Constant)",
     "authors": "L. Michaelis, M. L. Menten; translation K. A. Johnson, R. S. Goody", "year": 1913,
     "venue": "Biochem. Z. 49:333-369 (1913); Biochemistry 50:8264 (2011)",
     "url": "https://pubs.acs.org/doi/10.1021/bi201284u", "section": "rate equation of the ES-complex model",
     "verified": "Translation fetched from PMC3381512 on 2026-10-03; rate law incl. product terms confirmed."},
    ["v = Vmax S / (Km + S)", "fraction bound = S / (Km + S)", "v(Km) = Vmax/2", "S for v = f Vmax: S = f Km/(1-f)"],
    [
        {"label": "S = Km", "params": {"S": 2.0, "Vmax": 10.0, "Km": 2.0}, "expected": {"v": mm(2, 10, 2),
         "fraction_bound": 0.5}, "tol": 1e-6},
        {"label": "saturating", "params": {"S": 18.0, "Vmax": 10.0, "Km": 2.0},
         "expected": {"v": mm(18, 10, 2), "fraction_bound": 0.9}, "tol": 1e-6},
        {"label": "low substrate", "params": {"S": 0.2, "Vmax": 10.0, "Km": 2.0},
         "expected": {"v": mm(0.2, 10, 2), "linear_approx_Vmax_S_over_Km": 10 * 0.2 / 2}, "tol": 1e-4},
        {"label": "S for 90% of Vmax", "params": {"Km": 2.0, "f": 0.9}, "expected": {"S": 0.9 * 2 / 0.1},
         "tol": 1e-6},
    ],
    [
        "Rate law v = Vmax S/(Km + S) (Michaelis-Menten equation)",
        "Mechanism: enzyme and substrate form a complex; rate is proportional to complex concentration",
        "When S = Km the rate is exactly half of Vmax",
        "At low S the rate is approximately (Vmax/Km) S (first order); at high S it saturates at Vmax (zero order)",
        "Vmax = (catalytic rate constant) x (total enzyme)",
        "The original constant was a dissociation (equilibrium) constant of the enzyme-substrate compound",
    ],
    [
        "Allowing v > Vmax",
        "Half-maximal rate at S = Km/2 or at S = 2 Km",
        "Linear rate axis that never shows saturation because the S range is too small",
        "Attributing the quasi-steady-state (Briggs-Haldane 1925) derivation to the 1913 paper without note",
        "Missing units on S, Km and v",
    ],
    [
        q("At what substrate concentration is the rate half of Vmax?",
          ["S = Km", "S = 2 Km", "S = Km/2", "S = Vmax"], "A", 2),
        q("What happens to the rate at very high substrate concentration?",
          ["It grows without bound", "It approaches Vmax", "It drops to zero", "It equals Km"], "B", 3),
        q("In the Michaelis-Menten mechanism, the rate is proportional to:",
          ["Free substrate only", "The enzyme-substrate complex concentration", "The product concentration",
           "Temperature only"], "B", 1),
        q("Which equation gives the initial rate?",
          ["v = Vmax Km / S", "v = Vmax S/(Km + S)", "v = Km S/(Vmax + S)", "v = Vmax (1 - e^{-S})"], "B", 0),
        q("At very low substrate, the rate is approximately:",
          ["Vmax", "(Vmax/Km) S", "Km S", "Zero for all S"], "B", 3),
    ],
    invariants=["0 <= v < Vmax", "v monotonically increasing in S", "v(Km) == Vmax/2"],
)

# 5. Lotka-Volterra --------------------------------------------------------------------------------
lv = lv_simulate(1.0, 0.1, 1.5, 0.075, 10.0, 5.0, T=60.0)
lv2 = lv_simulate(1.0, 0.1, 1.5, 0.075, 21.0, 10.5, T=60.0)
case(
    "lotka_volterra",
    "https://www.nature.com/articles/118558a0",
    "Volterra (1926), Fluctuations in the abundance of a species considered mathematically (the Lotka-Volterra "
    "predator-prey model). Explain why prey and predator populations oscillate out of phase. Let the "
    "learner set the prey growth rate, predation rate, predator death rate, conversion rate and the "
    "initial populations. Show both populations over time and the phase-plane orbit around the "
    "coexistence equilibrium. Guide them through starting exactly at the equilibrium and starting far "
    "from it. Check that the equilibrium is (gamma/delta, alpha/beta), that the orbit closes, and that the "
    "time-averaged populations over one cycle equal the equilibrium values.",
    "Environmental engineering undergraduate who has seen first-order linear ODEs but not coupled nonlinear "
    "systems.",
    """
Source: V. Volterra, "Fluctuations in the Abundance of a Species considered Mathematically", Nature 118
(1926) 558-560, doi:10.1038/118558a0; the same equations were given by A. J. Lotka, Elements of
Physical Biology (1925). Paraphrased excerpt (publisher page is behind a login; content checked against
standard secondary summaries on 2026-10-03).

Volterra considers two species living together, one (the prey) feeding on resources that are always
available and the other (the predator) feeding only on the first. Writing x for the prey and y for the
predator density,
    dx/dt = alpha x - beta x y,      dy/dt = delta x y - gamma y,
where alpha is the prey's natural growth rate, beta the rate at which encounters remove prey, gamma
the predator's death rate without food and delta the rate at which eaten prey become new predators.
Volterra states three "laws": (1) the fluctuations are periodic, with a period depending only on the
coefficients and the initial conditions; (2) conservation of averages: the averages of the two
populations over a period are constant and equal to the equilibrium values x* = gamma/delta and
y* = alpha/beta, whatever the initial conditions; (3) if both species are destroyed uniformly and in
proportion to their numbers, the average prey population increases and the average predator
population decreases. A quantity V = delta x - gamma ln x + beta y - alpha ln y is conserved, so
orbits in the (x, y) plane are closed curves around the equilibrium.
""",
    {"title": "Fluctuations in the Abundance of a Species considered Mathematically", "authors": "V. Volterra",
     "year": 1926, "venue": "Nature 118:558-560", "url": "https://www.nature.com/articles/118558a0",
     "section": "predator-prey equations; three laws",
     "verified": "Nature page redirects to login for scripts; equations, equilibrium and conserved quantity "
                 "checked against Wikipedia 'Lotka-Volterra equations' on 2026-10-03."},
    ["dx/dt = alpha x - beta x y; dy/dt = delta x y - gamma y",
     "equilibrium (x*, y*) = (gamma/delta, alpha/beta)",
     "conserved V = delta x - gamma ln x + beta y - alpha ln y",
     "small-oscillation period ~ 2 pi / sqrt(alpha gamma)"],
    [
        {"label": "start away from equilibrium",
         "params": {"alpha": 1.0, "beta": 0.1, "gamma": 1.5, "delta": 0.075, "x0": 10, "y0": 5},
         "expected": {"equilibrium": [20.0, 10.0], "cycle_mean_prey": lv["mean_prey"],
                      "cycle_mean_pred": lv["mean_pred"], "period": lv["period"],
                      "prey_range": lv["prey_range"], "pred_range": lv["pred_range"],
                      "small_osc_period": 2 * math.pi / math.sqrt(1.0 * 1.5)},
         "tol": 0.02, "note": "RK4 dt=1e-3 over 60 time units; period applies to this large orbit"},
        {"label": "near equilibrium",
         "params": {"alpha": 1.0, "beta": 0.1, "gamma": 1.5, "delta": 0.075, "x0": 21, "y0": 10.5},
         "expected": {"cycle_mean_prey": lv2["mean_prey"], "cycle_mean_pred": lv2["mean_pred"],
                      "period": lv2["period"], "small_osc_period": 2 * math.pi / math.sqrt(1.5)},
         "tol": 0.02},
        {"label": "start at equilibrium",
         "params": {"alpha": 1.0, "beta": 0.1, "gamma": 1.5, "delta": 0.075, "x0": 20, "y0": 10},
         "expected": {"x(t)": 20.0, "y(t)": 10.0}, "tol": 1e-6, "note": "no motion"},
    ],
    [
        "Equations: dx/dt = alpha x - beta x y (prey), dy/dt = delta x y - gamma y (predator)",
        "Coexistence equilibrium at x* = gamma/delta, y* = alpha/beta",
        "Populations oscillate periodically, with the predator peak lagging the prey peak",
        "Average populations over a cycle equal the equilibrium values (Volterra's law of conservation of averages)",
        "Orbits are closed curves because V = delta x - gamma ln x + beta y - alpha ln y is conserved",
        "Starting exactly at equilibrium produces no oscillation",
    ],
    [
        "Equilibrium swapped, e.g. (alpha/beta, gamma/delta)",
        "Forward-Euler integration that makes orbits spiral outward and calls this real behaviour",
        "Claiming oscillations damp out to the equilibrium in this model",
        "Predator peak shown before the prey peak",
        "Negative populations from large time steps",
    ],
    [
        q("Where is the coexistence equilibrium for dx/dt = alpha x - beta x y, dy/dt = delta x y - gamma y?",
          ["(alpha/beta, gamma/delta)", "(gamma/delta, alpha/beta)", "(0, 0) only", "(alpha, gamma)"], "B", 1),
        q("How does the predator population peak relate to the prey peak?",
          ["It peaks first", "It peaks at the same time", "It lags behind the prey peak", "It never peaks"], "C", 2),
        q("What is the time-average of the prey population over one full cycle?",
          ["Its initial value", "Its maximum", "The equilibrium value gamma/delta", "Zero"], "C", 3),
        q("Why are the phase-plane orbits closed curves?",
          ["Because of numerical damping", "Because a quantity V is conserved along trajectories",
           "Because predators eventually die out", "Because the prey has a carrying capacity"], "B", 4),
        q("What happens if the system starts exactly at the equilibrium point?",
          ["Large oscillations", "Both populations stay constant", "Prey go extinct", "Predators explode"], "B", 5),
    ],
    invariants=["x,y > 0", "V conserved (relative drift < 1e-3 with a good integrator)",
                "cycle averages ~ (gamma/delta, alpha/beta)"],
)

# 6. Hardy-Weinberg ---------------------------------------------------------------------------------
def hw_next(AA, Aa, aa):
    pA = AA + Aa / 2
    return {"allele_A": pA, "next_AA": pA ** 2, "next_Aa": 2 * pA * (1 - pA), "next_aa": (1 - pA) ** 2}


case(
    "hardy_weinberg",
    "https://www.science.org/doi/10.1126/science.28.706.49",
    "Hardy (1908), Mendelian Proportions in a Mixed Population. Explain why genotype proportions reach a "
    "stable equilibrium after one generation of random mating and why a dominant allele does not "
    "automatically spread. Let the learner set the starting genotype proportions (AA, Aa, aa). Show allele "
    "frequencies, the next generation's genotype proportions, and the condition q^2 = p r in Hardy's "
    "notation. Guide them through a population of only AA and aa, and through a rare dominant allele. "
    "Check that genotype proportions sum to one, that allele frequency is unchanged across generations, "
    "and that the second generation equals the first equilibrium.",
    "Biomedical engineering undergraduate with only high-school biology.",
    """
Source: G. H. Hardy, "Mendelian Proportions in a Mixed Population", Science 28 (1908) 49-50,
doi:10.1126/science.28.706.49 (public domain). Paraphrase with short quotations; the original PDF
mirrors were unreachable from our network, so the content was checked against secondary summaries
(search results and PMC commentary) on 2026-10-03.

Hardy replies to the suggestion that a dominant character, such as brachydactyly, should be expected
to spread until three quarters of a population show it. Suppose a single pair of Mendelian characters
A and a, and that in one generation the pure dominants (AA), heterozygotes (Aa) and pure recessives
(aa) occur in the proportions p : 2q : r. With random mating, equal fertility and survival, and a
large population, the next generation's proportions are

    (p + q)^2 : 2(p + q)(q + r) : (q + r)^2,   or p1 : 2q1 : r1.

The distribution is unchanged from one generation to the next exactly when q^2 = p r. Hardy notes
that q1^2 = p1 r1 always holds, so whatever the starting proportions, the population reaches this
stable distribution after a single generation and stays there. Hence there is no tendency for a
dominant character to increase: a dominant allele that is rare stays rare. (Weinberg reached the same
result independently in 1908.) In modern notation, with allele frequencies p_A and p_a = 1 - p_A, the
equilibrium genotype frequencies are p_A^2, 2 p_A p_a and p_a^2.
""",
    {"title": "Mendelian Proportions in a Mixed Population", "authors": "G. H. Hardy", "year": 1908,
     "venue": "Science 28(706):49-50", "url": "https://www.science.org/doi/10.1126/science.28.706.49",
     "section": "whole letter", "verified": "Primary PDF unreachable (connection reset / 404 / 403); "
     "notation p:2q:r, next-generation formula and q^2 = pr confirmed via secondary sources 2026-10-03."},
    ["allele freq p_A = AA + Aa/2", "next generation: AA' = p_A^2, Aa' = 2 p_A (1-p_A), aa' = (1-p_A)^2",
     "Hardy notation p:2q:r stable iff q^2 = p r"],
    [
        {"label": "only homozygotes", "params": {"AA": 0.5, "Aa": 0.0, "aa": 0.5},
         "expected": hw_next(0.5, 0.0, 0.5), "tol": 1e-9, "note": "Hardy q = 0 so q^2 != pr; one generation fixes it"},
        {"label": "skewed start", "params": {"AA": 0.6, "Aa": 0.2, "aa": 0.2},
         "expected": dict(hw_next(0.6, 0.2, 0.2), hardy_check_q1sq=(0.42 / 2) ** 2, hardy_check_p1r1=0.49 * 0.09),
         "tol": 1e-9},
        {"label": "rare dominant allele", "params": {"AA": 0.0, "Aa": 0.02, "aa": 0.98},
         "expected": dict(hw_next(0.0, 0.02, 0.98), dominant_phenotype_next=1 - 0.99 ** 2), "tol": 1e-9,
         "note": "dominant phenotype stays ~2%, it does not rise to 75%"},
    ],
    [
        "Hardy's notation: genotype proportions p : 2q : r for AA : Aa : aa",
        "After one generation of random mating proportions are (p+q)^2 : 2(p+q)(q+r) : (q+r)^2",
        "Equilibrium condition q^2 = p r (modern form: p_A^2, 2 p_A p_a, p_a^2)",
        "Equilibrium is reached after a single generation and then stays fixed",
        "A dominant allele does not tend to increase in frequency just because it is dominant",
        "Assumptions: random mating, large population, no selection, mutation or migration",
    ],
    [
        "Claiming a dominant allele spreads to 3/4 of the population",
        "Taking several generations to reach equilibrium under random mating",
        "Allele frequency computed as AA + Aa instead of AA + Aa/2",
        "Confusing Hardy's 2q (heterozygote proportion) with the modern allele frequency q",
        "Omitting the assumptions (random mating, no selection, large population)",
    ],
    [
        q("Under random mating, how many generations does it take to reach Hardy-Weinberg proportions?",
          ["One", "About ten", "Infinitely many", "It depends on dominance"], "A", 3),
        q("In Hardy's notation p : 2q : r, the distribution is stable when:",
          ["p = r", "q^2 = p r", "p + q + r = 1", "q = 0"], "B", 2),
        q("Does a dominant allele tend to spread because it is dominant?",
          ["Yes, to 75% of the population", "Yes, to fixation", "No, its frequency stays constant without selection",
           "Only in small populations"], "C", 4),
        q("With allele frequency p_A = 0.7, the equilibrium heterozygote frequency is:",
          ["0.21", "0.42", "0.49", "0.09"], "B", 2),
        q("Which is NOT an assumption of the Hardy-Weinberg result?",
          ["Random mating", "No selection", "Large population", "The dominant allele is common"], "D", 5),
    ],
    invariants=["genotype proportions sum to 1", "allele frequency unchanged after mating",
                "second generation == first generation"],
)

# 7. Akerlof lemons ---------------------------------------------------------------------------------
def lemons(price, k, qmax=2.0):
    p = min(price, qmax)
    offered_fraction = p / qmax
    avg_q = p / 2
    buyer_value = k * avg_q
    return {"fraction_of_cars_offered": offered_fraction, "average_quality_offered": avg_q,
            "buyer_value_of_average_car": buyer_value, "trade_occurs": buyer_value >= price - 1e-12}


case(
    "lemons",
    "https://doi.org/10.2307/1879431",
    "Akerlof (1970), The Market for 'Lemons': Quality Uncertainty and the Market Mechanism, the formal "
    "used-car example. Explain adverse selection: when only sellers know quality, the price buyers will pay "
    "reflects average quality, so good cars leave the market. Let the learner set the price and how much "
    "more buyers value a car than sellers do. Show which cars are offered, their average quality, and "
    "whether buyers are willing to pay the price. Guide them through Akerlof's case (buyers value quality "
    "at 3/2 of sellers) and a case with a much larger valuation gap. Check that the average quality offered "
    "at price p is p/2 for quality uniform on [0, 2], and that with the 3/2 valuation no price leads to "
    "trade, while with symmetric information all cars would trade.",
    "Industrial engineering undergraduate who has taken an introductory microeconomics course.",
    """
Source: G. A. Akerlof, "The Market for 'Lemons': Quality Uncertainty and the Market Mechanism",
Quarterly Journal of Economics 84(3) (1970) 488-500, doi:10.2307/1879431. Paraphrased excerpt of
Section II's formal example; checked against the summary on Wikipedia ("The Market for Lemons") on
2026-10-03 because the PDF mirror returned 403.

There are two groups of traders. Group one owns N cars whose quality x is uniformly distributed
between 0 and 2, and has utility U1 = M + sum of x_i over cars owned (M is consumption of other
goods). Group two has utility U2 = M + sum of (3/2) x_i. Both groups know the average quality of cars
on the market but only the owner knows the quality of a particular car. At price p, group-one owners
supply the cars whose quality is at most p, so the supply is pN/2 and the average quality of cars
offered is p/2. A buyer from group two values such an average car at (3/2)(p/2) = 3p/4, which is less
than p, so at no price will any trade take place, even though at any price between 0 and 3 there
would be traders willing to buy and sell. If instead quality were known to everyone (symmetric
information), cars of every quality would trade at prices between the two valuations, and both
groups would gain. Bad cars drive out good: the presence of dishonest or low-quality sellers can
destroy the market for honest high-quality sellers.
""",
    {"title": "The Market for 'Lemons': Quality Uncertainty and the Market Mechanism", "authors": "G. A. Akerlof",
     "year": 1970, "venue": "Quarterly Journal of Economics 84(3):488-500", "url": "https://doi.org/10.2307/1879431",
     "section": "Section II (example with automobiles; two groups of traders)",
     "verified": "Utility functions, supply p N/2, average quality p/2 and no-trade result confirmed via "
                 "Wikipedia summary 2026-10-03 (PDF mirror 403)."},
    ["sellers supply cars with x <= p (x ~ U[0,2]): fraction offered = p/2, average quality = p/2",
     "buyer value of average offered car = k * p/2 (Akerlof: k = 3/2)", "trade iff k p/2 >= p  <=> k >= 2"],
    [
        {"label": "Akerlof k = 3/2, p = 1", "params": {"price": 1.0, "buyer_multiplier": 1.5},
         "expected": lemons(1.0, 1.5), "tol": 1e-9},
        {"label": "Akerlof k = 3/2, p = 2 (all cars offered)", "params": {"price": 2.0, "buyer_multiplier": 1.5},
         "expected": lemons(2.0, 1.5), "tol": 1e-9},
        {"label": "large gap k = 2.5, p = 1.2", "params": {"price": 1.2, "buyer_multiplier": 2.5},
         "expected": lemons(1.2, 2.5), "tol": 1e-9},
        {"label": "symmetric information surplus per car (k = 1.5)", "params": {"buyer_multiplier": 1.5},
         "expected": {"mean_gain_per_car_if_quality_known": (1.5 - 1) * 1.0, "gain_under_asymmetry": 0.0},
         "tol": 1e-9, "note": "mean quality 1, gain (k-1)*E[x]"},
    ],
    [
        "Quality is known to the seller but not the buyer (asymmetric information)",
        "At price p only cars with quality <= p are offered, so average quality offered is p/2",
        "With buyers valuing quality at 3/2, the average car is worth 3p/4 < p, so no trade occurs at any price",
        "With symmetric information every car would trade and both sides would gain",
        "Low-quality cars drive high-quality cars out of the market (adverse selection)",
        "Trade only survives here if buyers value quality at least twice as much as sellers (k >= 2)",
    ],
    [
        "Showing the market clearing at a positive price with k = 3/2",
        "Average quality offered computed as 1 (the population mean) instead of p/2",
        "Confusing adverse selection with moral hazard",
        "Claiming the problem disappears simply by lowering the price",
        "Omitting that sellers know quality but buyers only know the average",
    ],
    [
        q("If sellers' car quality is uniform on [0, 2] and the price is p (p <= 2), what is the average quality of "
          "cars offered?", ["1", "p", "p/2", "2p"], "C", 1),
        q("In Akerlof's example where buyers value quality at 3/2, how much trade happens?",
          ["All cars trade", "Half the cars trade", "No trade at any price", "Only lemons trade"], "C", 2),
        q("What would happen if buyers could observe each car's quality?",
          ["No trade", "Every car could trade with gains to both sides", "Only good cars trade", "Prices would be zero"],
          "B", 3),
        q("The core informational problem in the lemons market is:",
          ["Buyers know more than sellers", "Sellers know quality, buyers only know the average",
           "Nobody knows the price", "Sellers are irrational"], "B", 0),
        q("In this uniform model, how large must the buyer-to-seller valuation ratio k be for trade?",
          ["k >= 1", "k >= 1.5", "k >= 2", "Any k > 0"], "C", 5),
    ],
    invariants=["avg quality offered = min(p,2)/2", "trade iff k*avg >= p"],
)

# 8. Black-Scholes ---------------------------------------------------------------------------------
case(
    "black_scholes",
    "https://www.journals.uchicago.edu/doi/10.1086/260062",
    "Black and Scholes (1973), The Pricing of Options and Corporate Liabilities, the option valuation formula. "
    "Explain how a European call price depends on the stock price, strike, time to expiry, interest rate "
    "and volatility, using the closed-form formula. Let the learner change each input. Show d1, d2, N(d1), "
    "N(d2), the call price, and the price curve against stock price next to the payoff at expiry. Guide "
    "them through increasing volatility and through an option far in the money. Check put-call parity, "
    "that the price lies between max(S - K e^{-rT}, 0) and S, and that the call price rises with volatility.",
    "Electrical engineering junior comfortable with calculus and the normal distribution but with no "
    "finance background.",
    """
Source: F. Black and M. Scholes, "The Pricing of Options and Corporate Liabilities", Journal of
Political Economy 81(3) (1973) 637-654, doi:10.1086/260062. Paraphrased excerpt (the scanned JSTOR
copy we fetched has no text layer; content is the paper's well-known Eq. (13)).

The paper assumes ideal conditions: the short-term interest rate r is known and constant; the stock
price follows a random walk in continuous time with variance rate proportional to the square of the
price, so its distribution at the end of any finite interval is log-normal, with constant variance
rate v^2; the stock pays no dividends; the option is European (exercised only at maturity); there are
no transaction costs; borrowing at the short rate and short selling are allowed. Under these
conditions a hedged position of stock and options can be formed whose value does not depend on the
stock price, so it must earn the riskless rate. This leads to a differential equation for the option
value whose solution, for a call with exercise price c and maturity t*, is

    w(x, t) = x N(d1) - c e^{r(t - t*)} N(d2),
    d1 = [ln(x/c) + (r + v^2/2)(t* - t)] / (v sqrt(t* - t)),   d2 = d1 - v sqrt(t* - t),

where x is the stock price and N is the cumulative normal distribution. In modern notation:
C = S N(d1) - K e^{-rT} N(d2). The option value increases with the stock price, the time to maturity,
the interest rate and the variance rate of the stock's return, and it does not depend on the
stock's expected return.
""",
    {"title": "The Pricing of Options and Corporate Liabilities", "authors": "F. Black, M. Scholes", "year": 1973,
     "venue": "Journal of Political Economy 81(3):637-654", "url": "https://www.journals.uchicago.edu/doi/10.1086/260062",
     "section": "Eq. (13) and the 'ideal conditions'",
     "verified": "Scanned PDF (Princeton course mirror) fetched 2026-10-03: title/venue confirmed; equation "
                 "content from the standard statement of Eq. (13)."},
    ["d1 = [ln(S/K) + (r + sigma^2/2) T] / (sigma sqrt T); d2 = d1 - sigma sqrt T",
     "C = S N(d1) - K e^{-rT} N(d2); P = K e^{-rT} N(-d2) - S N(-d1)", "C - P = S - K e^{-rT}"],
    [
        {"label": "at the money", "params": {"S": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1},
         "expected": black_scholes(100, 100, 0.05, 0.2, 1), "tol": 1e-3},
        {"label": "higher volatility", "params": {"S": 100, "K": 100, "r": 0.05, "sigma": 0.4, "T": 1},
         "expected": black_scholes(100, 100, 0.05, 0.4, 1), "tol": 1e-3},
        {"label": "deep in the money", "params": {"S": 150, "K": 100, "r": 0.05, "sigma": 0.2, "T": 0.5},
         "expected": black_scholes(150, 100, 0.05, 0.2, 0.5), "tol": 1e-3},
    ],
    [
        "Call price C = S N(d1) - K e^{-rT} N(d2) with the stated d1, d2",
        "Assumptions: constant r and volatility, log-normal prices, no dividends, European exercise, no "
        "transaction costs",
        "Derived from a riskless hedge of stock and option, which must earn the risk-free rate",
        "Price does not depend on the stock's expected return",
        "Higher volatility raises the call price",
        "Put-call parity C - P = S - K e^{-rT}",
    ],
    [
        "Using N'(d) (density) instead of N(d) (cumulative)",
        "Forgetting to discount the strike (K instead of K e^{-rT})",
        "Volatility entered as percent (20) instead of 0.2",
        "Claiming price depends on the expected return of the stock",
        "Allowing T = 0 or sigma = 0 to produce NaN instead of the payoff limit",
    ],
    [
        q("Which formula gives the Black-Scholes European call price?",
          ["C = S N(d1) - K e^{-rT} N(d2)", "C = max(S - K, 0)", "C = S e^{rT} - K", "C = K N(d1) - S N(d2)"], "A", 0),
        q("What happens to the call price when volatility increases (all else fixed)?",
          ["It decreases", "It increases", "It is unchanged", "It becomes negative"], "B", 4),
        q("Which input does NOT appear in the Black-Scholes price?",
          ["Risk-free rate", "Volatility", "The stock's expected return", "Time to expiry"], "C", 3),
        q("Black and Scholes derive the price by constructing:",
          ["A portfolio that tracks the market index", "A riskless hedge of stock and option",
           "A Monte Carlo simulation", "A regression on past prices"], "B", 2),
        q("Put-call parity states that C - P equals:",
          ["0", "K - S", "S - K e^{-rT}", "sigma sqrt T"], "C", 5),
    ],
    invariants=["max(S-K e^{-rT},0) <= C <= S", "C increasing in S and sigma", "C - P == S - K e^{-rT}"],
)

# 9. Schelling --------------------------------------------------------------------------------------
sch = {}
for thr in (0.0, 0.3, 0.5):
    runs = [schelling_sim(seed, thr=thr) for seed in range(12)]
    sch[thr] = {"mean_like_initial": sum(r[0] for r in runs) / len(runs),
                "mean_like_final": sum(r[1] for r in runs) / len(runs),
                "mean_like_final_min": min(r[1] for r in runs),
                "mean_like_final_max": max(r[1] for r in runs),
                "mean_rounds": sum(r[2] for r in runs) / len(runs),
                "unhappy_at_end_max": max(r[3] for r in runs)}
case(
    "schelling",
    "https://doi.org/10.1080/0022250X.1971.9989794",
    "Schelling (1971), Dynamic Models of Segregation, the spatial proximity model. Explain how mild individual "
    "preferences about neighbours can produce strong overall segregation. Use a small grid of two groups "
    "with some empty cells and a seeded random start. Let the learner set the share of like neighbours "
    "each agent wants, the share of empty cells, and the random seed, and step or play the dynamics. Show "
    "the grid, the number of unhappy agents, and the average share of like neighbours over time. Guide "
    "them through a tolerant threshold near one third and a stricter threshold near one half. Check that "
    "with threshold 0 nobody moves, that agent counts are conserved, and that the run stops when nobody "
    "is unhappy.",
    "Civil engineering undergraduate interested in urban planning; no programming experience assumed.",
    """
Source: T. C. Schelling, "Dynamic Models of Segregation", Journal of Mathematical Sociology 1 (1971)
143-186, doi:10.1080/0022250X.1971.9989794. Paraphrased excerpt, checked against a scanned copy
(https://www.stat.berkeley.edu/~aldous/157/Papers/Schelling_Seg_Models.pdf) on 2026-10-03.

Schelling studies how individual choices about where to live can produce collective segregation that
nobody intends. People belong to one of two recognisable groups (he uses stars and zeros). In the
spatial proximity models each person defines a neighbourhood by reference to his own location: in the
linear model, the people within a fixed distance on each side; in the two-dimensional checkerboard
model, the surrounding squares. An individual moves if he is not content with the colour mix of his
neighbourhood, for example if fewer than a given fraction of his neighbours are like himself, and he
moves to the nearest place where the mix does meet his demands. Everyone of a given colour is assumed
to have the same preferences. Moves change the neighbourhoods of others, which can make further people
discontent, producing chain reactions. Starting from a random mixture, the process typically ends in
clusters that are much more segregated than any individual's demand: even a population content to
live in a 50:50 mixture ends up highly separated. Schelling also analyses a "bounded neighbourhood"
model in which people care about the colour ratio of a whole common area, with varying tolerances;
there, tipping can make a neighbourhood become entirely one colour.
""",
    {"title": "Dynamic Models of Segregation", "authors": "T. C. Schelling", "year": 1971,
     "venue": "Journal of Mathematical Sociology 1(2):143-186", "url": "https://doi.org/10.1080/0022250X.1971.9989794",
     "section": "spatial proximity models (linear and checkerboard); bounded-neighbourhood model",
     "verified": "Scanned PDF fetched 2026-10-03 (OCR text); movement rule and qualitative results confirmed."},
    ["like_fraction(agent) = like occupied neighbours / occupied neighbours (Moore neighbourhood)",
     "agent unhappy iff like_fraction < threshold; unhappy agents move to empty cells",
     "segregation measure = mean like_fraction over agents"],
    [
        {"label": f"threshold {thr}",
         "params": {"grid": "20x20 torus", "empty_share": 0.1, "threshold": thr, "neighbourhood": "Moore (8)",
                    "move_rule": "random empty cell", "seeds": "0..11 (Python random)"},
         "expected": sch[thr], "tol": "statistical: compare ranges, not exact values",
         "note": "implementation-dependent; a page using other rules should still show final like-share "
                 "well above the initial ~0.5 for threshold 0.3-0.5, and exactly no moves for threshold 0"}
        for thr in (0.0, 0.3, 0.5)
    ] + [
        {"label": "single-cell check", "params": {"like_neighbours": 3, "occupied_neighbours": 8},
         "expected": {"like_fraction": 0.375, "happy_at_threshold_0.3": True, "happy_at_threshold_0.5": False},
         "tol": 1e-9},
    ],
    [
        "Each agent moves if the share of like neighbours is below its threshold",
        "Mild preferences (e.g. wanting about one third like neighbours) produce strong segregation",
        "The collective outcome is more segregated than any individual demands (micromotives vs macrobehaviour)",
        "Moves change other agents' neighbourhoods and can trigger chain reactions",
        "Starting from a random mix, like-neighbour share rises well above the initial ~50%",
        "Threshold 0 means nobody ever moves",
    ],
    [
        "Using Math.random without a seed so a 'Try it' preset is not reproducible",
        "Counting empty cells as unlike neighbours (changes the dynamics; must be stated if used)",
        "Agent counts not conserved after moves",
        "Claiming segregation requires strong preferences or prejudice",
        "Animation that never stops even when no agent is unhappy",
    ],
    [
        q("When does an agent move in Schelling's model?",
          ["Every round regardless", "When the share of like neighbours is below its threshold",
           "When it has no neighbours", "When the grid is full"], "B", 0),
        q("What is Schelling's key finding about preferences and segregation?",
          ["Only strong prejudice produces segregation", "Mild individual preferences can produce strong "
           "collective segregation", "Segregation never emerges from random starts", "Segregation decreases over time"], "B", 1),
        q("Why can one move cause further moves?",
          ["Agents copy each other", "A move changes the neighbourhood mix of other agents",
           "The threshold increases every round", "Empty cells disappear"], "B", 3),
        q("With a threshold of 0, what happens?",
          ["Everyone moves", "Nobody moves", "Only one group moves", "The grid becomes empty"], "B", 5),
        q("Starting from a random mix, the average share of like neighbours typically:",
          ["Stays at about 50%", "Rises well above 50%", "Drops to 0", "Oscillates forever"], "B", 4),
    ],
    invariants=["agent counts per group conserved", "threshold 0 -> zero moves", "terminates when no unhappy agents",
                "same seed -> same run"],
)

# 10. Nyquist ---------------------------------------------------------------------------------------
case(
    "nyquist",
    "https://ieeexplore.ieee.org/document/5055024",
    "Nyquist (1928), Certain Topics in Telegraph Transmission Theory: the limit of 2B independent values per "
    "second in a band B, read today as the sampling criterion. Explain sampling and aliasing using a single "
    "sinusoid. Let the learner set the signal frequency and the sampling rate. Show the continuous signal, "
    "the samples, and the lowest-frequency sinusoid that passes through the same samples. Guide them "
    "through sampling well above twice the signal frequency and below it. Check that the apparent "
    "frequency equals the true one when fs > 2f, that a 7 Hz tone sampled at 10 Hz appears at 3 Hz, and "
    "be honest about what Nyquist's paper did and did not state.",
    "Electrical engineering sophomore taking a first signals-and-systems course.",
    """
Source: H. Nyquist, "Certain Topics in Telegraph Transmission Theory", Transactions of the AIEE 47
(1928) 617-644 (reprinted Proc. IEEE 90(2), 2002). Paraphrased excerpt (public domain original; the
reprint PDF was not reachable, so content was checked against secondary accounts on 2026-10-03).

Nyquist analysed telegraph signalling, in which a message is sent as a sequence of pulse values
("signal elements") at a fixed rate. He showed that the number of independent pulse values that can
be put through a channel per unit time is limited to twice the bandwidth of the channel: with
bandwidth B (in cycles per second), at most 2B independent values per second can be sent, and
conversely 2B values per second are enough to determine a signal limited to that band. The interval
1/(2B) later became known as the Nyquist interval. Nyquist did not state the sampling theorem in its
modern form; the theorem that a signal containing no frequencies above B is completely determined by
samples taken 1/(2B) apart is usually credited to Whittaker, Kotelnikov and Shannon (1949).

Modern reading used in teaching: sampling a sinusoid of frequency f at rate fs produces samples that
are identical to those of a sinusoid at |f - k fs| for any integer k. If fs > 2f the lowest such
frequency is f itself; if fs < 2f, the samples look like a lower-frequency "alias".
""",
    {"title": "Certain Topics in Telegraph Transmission Theory", "authors": "H. Nyquist", "year": 1928,
     "venue": "Transactions of the AIEE 47(2):617-644", "url": "https://ieeexplore.ieee.org/document/5055024",
     "section": "signalling-speed / bandwidth result (2B values per second)",
     "verified": "Original/reprint PDFs unreachable (404/paywall); attribution checked against Wikipedia "
                 "'Nyquist-Shannon sampling theorem' on 2026-10-03, which notes Nyquist did not state the theorem "
                 "explicitly."},
    ["apparent frequency f_a = |f - fs * round(f/fs)|", "no aliasing iff fs > 2 f (Nyquist rate 2B)",
     "Nyquist interval = 1/(2B)"],
    [
        {"label": "well sampled", "params": {"f_Hz": 3, "fs_Hz": 10}, "expected": {"apparent_Hz": alias(3, 10),
         "aliased": False, "nyquist_rate_Hz": 6}, "tol": 1e-9},
        {"label": "under-sampled", "params": {"f_Hz": 7, "fs_Hz": 10}, "expected": {"apparent_Hz": alias(7, 10),
         "aliased": True, "nyquist_rate_Hz": 14}, "tol": 1e-9},
        {"label": "above fs", "params": {"f_Hz": 12, "fs_Hz": 10}, "expected": {"apparent_Hz": alias(12, 10),
         "aliased": True}, "tol": 1e-9},
        {"label": "exactly fs/2", "params": {"f_Hz": 5, "fs_Hz": 10, "phase": 0},
         "expected": {"apparent_Hz": 5, "note": "sin samples are all zero: ambiguous; need fs strictly > 2f"},
         "tol": 1e-9},
        {"label": "channel capacity in values", "params": {"B_Hz": 3000}, "expected": {"max_independent_values_per_s": 6000,
         "nyquist_interval_s": 1 / 6000}, "tol": 1e-12},
    ],
    [
        "A channel of bandwidth B can carry at most 2B independent values per second (Nyquist 1928)",
        "To avoid aliasing, sample faster than twice the highest frequency: fs > 2 f_max",
        "Under-sampled sinusoids appear at an alias frequency |f - k fs|",
        "A 7 Hz tone sampled at 10 Hz looks like 3 Hz",
        "The sampling theorem in modern form is due to Whittaker, Kotelnikov and Shannon; Nyquist did not state it explicitly",
        "Sampling exactly at 2f can give all-zero or ambiguous samples",
    ],
    [
        "Claiming Nyquist's 1928 paper proved the sampling theorem as now stated",
        "Alias formula giving negative frequencies or f mod fs without folding (7 mod 10 = 7, wrong)",
        "Saying fs = 2f is always sufficient",
        "Drawing the reconstructed curve through the samples with the true (not the alias) frequency when aliased",
        "Confusing Nyquist frequency (fs/2) with Nyquist rate (2B)",
    ],
    [
        q("A 7 Hz sine sampled at 10 samples per second appears to have frequency:",
          ["7 Hz", "3 Hz", "17 Hz", "10 Hz"], "B", 3),
        q("What sampling rate avoids aliasing for a signal whose highest frequency is f?",
          ["Any rate above f", "A rate above 2f", "Exactly f/2", "Exactly f"], "B", 1),
        q("What did Nyquist's 1928 paper establish?",
          ["The fast Fourier transform", "At most 2B independent values per second through bandwidth B",
           "The entropy of a source", "Quantisation noise formulas"], "B", 0),
        q("Who is usually credited with the sampling theorem in its modern form?",
          ["Nyquist alone in 1928", "Whittaker, Kotelnikov and Shannon", "Fourier", "Hartley"], "B", 4),
        q("Under-sampled tones show up at which frequency?",
          ["|f - k fs| for some integer k (the alias)", "f + fs always", "0 Hz", "fs/2 always"], "A", 2),
    ],
    invariants=["0 <= apparent <= fs/2", "apparent == f iff f < fs/2"],
)

# 11. Adam bias correction ---------------------------------------------------------------------------
ad_const = adam_trace([1.0] * 10)
ad_seq = adam_trace([1.0, 0.5, -0.2])
case(
    "adam",
    "https://arxiv.org/abs/1412.6980",
    "Adam: A Method for Stochastic Optimization (Kingma and Ba), Algorithm 1 and Section 3. Explain the "
    "initialization bias of the moving averages and how the bias correction removes it, using a short "
    "sequence of scalar gradients. Let the learner edit the gradient sequence, beta1, beta2 and the step "
    "size, and toggle bias correction. Show m_t, v_t, the corrected estimates and the resulting step at "
    "each time step. Guide them through a constant gradient and through beta2 close to one. Check that "
    "with a constant gradient the corrected estimates equal the gradient and its square at every step, "
    "and that the first corrected step has magnitude close to the step size alpha. Do not train a "
    "neural network.",
    "Computer engineering undergraduate who has used plain stochastic gradient descent but not Adam.",
    """
Source: D. P. Kingma and J. Ba, "Adam: A Method for Stochastic Optimization", ICLR 2015, arXiv:1412.6980
(v9). Paraphrased excerpt of Section 2 (Algorithm 1) and Section 3, checked against the arXiv PDF on
2026-10-03.

Adam keeps exponential moving averages of the gradient (m_t) and of the squared gradient (v_t), with
decay rates beta1, beta2 in [0, 1). These estimate the first moment (mean) and the second raw moment
(uncentered variance) of the gradient. Algorithm 1, with g_t the gradient at step t:
    m_t = beta1 m_{t-1} + (1 - beta1) g_t
    v_t = beta2 v_{t-1} + (1 - beta2) g_t^2
    m_hat_t = m_t / (1 - beta1^t),   v_hat_t = v_t / (1 - beta2^t)
    theta_t = theta_{t-1} - alpha m_hat_t / (sqrt(v_hat_t) + epsilon)
Good default settings for the tested problems are alpha = 0.001, beta1 = 0.9, beta2 = 0.999 and
epsilon = 1e-8. Because the moving averages are initialised as zeros, the moment estimates are biased
towards zero, especially during the initial timesteps and especially when the decay rates are close
to 1. Section 3 shows that E[v_t] = E[g_t^2] (1 - beta2^t) + zeta (zeta small if the second moment is
stationary), so dividing by (1 - beta2^t) corrects the bias; the same holds for m_t. Without the
correction, when beta2 is close to 1 the early steps would be much too large.
""",
    {"title": "Adam: A Method for Stochastic Optimization", "authors": "D. P. Kingma, J. Ba", "year": 2014,
     "venue": "ICLR 2015 (arXiv:1412.6980 v9)", "url": "https://arxiv.org/abs/1412.6980",
     "section": "Algorithm 1; Section 3 'Initialization bias correction'",
     "verified": "arXiv PDF text extracted 2026-10-03; defaults and bias-correction rationale confirmed."},
    ["m_t = b1 m_{t-1} + (1-b1) g_t ; v_t = b2 v_{t-1} + (1-b2) g_t^2",
     "m_hat = m_t/(1-b1^t); v_hat = v_t/(1-b2^t)", "step = alpha m_hat/(sqrt(v_hat)+eps)"],
    [
        {"label": "constant gradient g = 1, t = 1..10 (defaults)", "params": {"grads": [1.0] * 10, "beta1": 0.9,
         "beta2": 0.999, "alpha": 0.001, "eps": 1e-8},
         "expected": {"t1": ad_const[0], "t10": ad_const[9]}, "tol": 1e-6,
         "note": "corrected m_hat = 1, v_hat = 1, step ~ alpha; uncorrected step at t=1 ~ 3.16 alpha"},
        {"label": "sequence [1, 0.5, -0.2]", "params": {"grads": [1.0, 0.5, -0.2], "beta1": 0.9, "beta2": 0.999,
         "alpha": 0.001, "eps": 1e-8}, "expected": {"steps": ad_seq}, "tol": 1e-6},
        {"label": "bias factors at t = 1", "params": {"beta1": 0.9, "beta2": 0.999},
         "expected": {"1-beta1^1": 0.1, "1-beta2^1": 0.001}, "tol": 1e-12},
    ],
    [
        "m_t and v_t are exponential moving averages of g and g^2, initialised at zero",
        "Zero initialisation biases the estimates towards zero, especially early and when beta is close to 1",
        "Bias correction divides by (1 - beta1^t) and (1 - beta2^t)",
        "With a constant gradient, corrected estimates equal g and g^2 exactly",
        "Update: theta <- theta - alpha m_hat / (sqrt(v_hat) + epsilon)",
        "Defaults: alpha = 0.001, beta1 = 0.9, beta2 = 0.999, epsilon = 1e-8",
    ],
    [
        "Applying the correction with t starting at 0 (division by zero) or using a fixed t",
        "Using (1 - beta)^t instead of 1 - beta^t",
        "Taking sqrt of m_hat or forgetting sqrt on v_hat",
        "Claiming bias correction changes the long-run behaviour (its effect vanishes as t grows)",
        "Mixing per-step and cumulative step sizes in the plot",
    ],
    [
        q("Why are Adam's raw moment estimates biased at the start?",
          ["Gradients are noisy", "They are initialised at zero", "The learning rate is too small",
           "epsilon is too large"], "B", 1),
        q("What does Adam divide m_t by to correct the bias?", ["1 - beta1^t", "(1 - beta1)^t", "beta1^t", "t"], "A", 2),
        q("For a constant gradient g, what is the bias-corrected first moment m_hat_t?",
          ["g (1 - beta1^t)", "g", "0", "g / t"], "B", 3),
        q("Adam's default beta2 is:", ["0.9", "0.99", "0.999", "0.5"], "C", 5),
        q("Without bias correction and with beta2 = 0.999, early update steps are:",
          ["Too small", "Much too large", "Exactly alpha", "Zero"], "B", 1),
    ],
    invariants=["constant g -> m_hat == g and v_hat == g^2", "correction factor -> 1 as t grows"],
)

# 12. DDPM forward process ----------------------------------------------------------------------------
ddpm_rows = []
for t in (1, 100, 500, 1000):
    ab = ddpm_alpha_bar(t)
    ddpm_rows.append({"t": t, "beta_t": 1e-4 + (0.02 - 1e-4) * (t - 1) / 999, "alpha_bar": ab,
                      "signal_coef_sqrt_alpha_bar": math.sqrt(ab), "noise_std_sqrt_1m_alpha_bar": math.sqrt(1 - ab),
                      "x_t_for_x0_1_eps_0.5": math.sqrt(ab) * 1.0 + math.sqrt(1 - ab) * 0.5,
                      "snr_alpha_bar_over_1m": ab / (1 - ab)})
case(
    "ddpm",
    "https://arxiv.org/abs/2006.11239",
    "Denoising Diffusion Probabilistic Models (Ho, Jain and Abbeel 2020), Section 2, the forward (noising) "
    "process and its closed form q(x_t | x_0). Explain how repeatedly adding small Gaussian noise shrinks the "
    "signal and why x_t can be sampled directly from x_0. Use a one-dimensional data value (or a few points) "
    "and a linear beta schedule. Let the learner change the timestep t, the schedule endpoints, the number "
    "of steps T, the data value and the noise seed. Show beta_t, alpha-bar_t, the signal and noise "
    "coefficients and the resulting distribution of x_t. Guide them through small t and t near T. Check "
    "that sqrt(alpha-bar)^2 + sqrt(1-alpha-bar)^2 = 1, that alpha-bar decreases with t, and that iterating "
    "the one-step process matches the closed form. Do not train a model.",
    "Engineering undergraduate familiar with the Gaussian distribution and expectation, new to generative models.",
    """
Source: J. Ho, A. Jain, P. Abbeel, "Denoising Diffusion Probabilistic Models", NeurIPS 2020,
arXiv:2006.11239. Paraphrased excerpt of Section 2 (Eqs. 2 and 4) and Section 4, checked against the
arXiv PDF on 2026-10-03.

Diffusion models are latent variable models whose approximate posterior q(x_1:T | x_0), called the
forward or diffusion process, is fixed to a Markov chain that gradually adds Gaussian noise to the
data according to a variance schedule beta_1, ..., beta_T:
    q(x_t | x_{t-1}) = N(x_t; sqrt(1 - beta_t) x_{t-1}, beta_t I)            (Eq. 2)
A notable property of the forward process is that it admits sampling x_t at an arbitrary timestep t
in closed form. Using alpha_t = 1 - beta_t and alpha_bar_t = product over s = 1..t of alpha_s,
    q(x_t | x_0) = N(x_t; sqrt(alpha_bar_t) x_0, (1 - alpha_bar_t) I)        (Eq. 4)
so x_t = sqrt(alpha_bar_t) x_0 + sqrt(1 - alpha_bar_t) epsilon with epsilon ~ N(0, I). This makes
training efficient, because random terms of the training objective can be optimised at any t.
In the experiments (Section 4) the authors set T = 1000 and the forward variances to constants
increasing linearly from beta_1 = 1e-4 to beta_T = 0.02, small relative to data scaled to [-1, 1],
so that the signal-to-noise ratio at x_T is as small as possible (x_T is close to pure noise).
""",
    {"title": "Denoising Diffusion Probabilistic Models", "authors": "J. Ho, A. Jain, P. Abbeel", "year": 2020,
     "venue": "NeurIPS 2020 (arXiv:2006.11239)", "url": "https://arxiv.org/abs/2006.11239",
     "section": "Section 2, Eqs. (2) and (4); Section 4 schedule",
     "verified": "arXiv PDF text extracted 2026-10-03; Eq. 2, Eq. 4 and the linear 1e-4..0.02, T=1000 schedule confirmed."},
    ["beta_t = beta_1 + (beta_T - beta_1)(t-1)/(T-1)", "alpha_bar_t = prod_{s<=t} (1 - beta_s)",
     "x_t = sqrt(alpha_bar_t) x0 + sqrt(1 - alpha_bar_t) eps"],
    [
        {"label": f"t = {row['t']} (T=1000, linear 1e-4..0.02)", "params": {"t": row["t"], "T": 1000, "beta_1": 1e-4,
         "beta_T": 0.02, "x0": 1.0, "eps": 0.5}, "expected": row, "tol": 1e-3}
        for row in ddpm_rows
    ],
    [
        "Forward step q(x_t | x_{t-1}) = N(sqrt(1-beta_t) x_{t-1}, beta_t I)",
        "Closed form q(x_t | x_0) = N(sqrt(alpha_bar_t) x_0, (1 - alpha_bar_t) I) with alpha_bar_t = prod (1 - beta_s)",
        "x_t can be sampled directly at any t without simulating all previous steps",
        "alpha_bar_t decreases with t, so the signal shrinks and noise grows; x_T is nearly pure noise",
        "Paper schedule: T = 1000, beta linear from 1e-4 to 0.02",
        "Variance is preserved for unit-variance data: alpha_bar + (1 - alpha_bar) = 1",
    ],
    [
        "Using sum of betas instead of product of (1 - beta)",
        "Using alpha_bar (not its square root) as the signal coefficient, or variance vs std confusion",
        "Indexing the schedule from t = 0 so beta_1 is skipped",
        "Saying x_T is exactly Gaussian noise (it is only approximately so)",
        "Describing the reverse (learned) process as if it were the forward process",
    ],
    [
        q("How is alpha_bar_t defined?",
          ["Sum of beta_s up to t", "Product of (1 - beta_s) for s = 1..t", "1 - beta_t", "beta_t / t"], "B", 1),
        q("What is the mean of q(x_t | x_0)?", ["x_0", "sqrt(alpha_bar_t) x_0", "alpha_bar_t x_0", "0"], "B", 1),
        q("Why is the closed form useful?",
          ["It removes the need for noise", "It lets you sample x_t at any t directly from x_0",
           "It makes the reverse process exact", "It trains the network"], "B", 2),
        q("Which beta schedule does the paper use?",
          ["Constant 0.5", "Linear from 1e-4 to 0.02 with T = 1000", "Cosine with T = 10", "Exponential to 1"], "B", 4),
        q("As t approaches T, x_t becomes:",
          ["Closer to x_0", "Nearly pure Gaussian noise", "Exactly zero", "Undefined"], "B", 3),
    ],
    invariants=["alpha_bar decreasing in t", "signal^2 + noise^2 == 1", "iterated one-step == closed form in distribution"],
)

# ---------------------------------------------------------------------------------------------
# Benchmark-derived cases (generalisation). See eval/BENCHMARKS.md for provenance + licences.
# ---------------------------------------------------------------------------------------------

def lj(r, s, e):
    q6 = (s / r) ** 6
    return 4 * e * (q6 * q6 - q6)


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


case(
    "bench_lennard_jones",
    "https://doi.org/10.1098/rspa.1924.0082",
    "Lennard-Jones (1924), On the determination of molecular fields, the 12-6 pair potential "
    "V(r) = 4 epsilon [(sigma/r)^12 - (sigma/r)^6]. Explain how short-range repulsion and longer-range "
    "attraction combine into a potential well. Let the learner change sigma, epsilon and the separation r, "
    "and place up to four atoms on a line to sum pair energies. Show the repulsive and attractive terms "
    "separately, their sum, the force, and the total energy of the small cluster. Guide them through r = "
    "sigma and the minimum. Check that V(sigma) = 0, that the minimum is at r = 2^(1/6) sigma with depth "
    "-epsilon, and that the force is zero there.",
    "Materials engineering undergraduate who knows Coulomb's law and potential energy but no molecular simulation.",
    """
Benchmark provenance: SciCode problem 47 "Internal_Energy", step 47.1 (and 47.2-47.3), Apache-2.0,
https://huggingface.co/datasets/SciCode1/SciCode. Background paraphrased from the scientist-written
step background; primary source: J. E. Lennard-Jones, Proc. R. Soc. Lond. A 106 (1924) 463-477.

The Lennard-Jones potential models the soft repulsive and attractive (van der Waals) interactions
between electrically neutral atoms or molecules. Its common form is

    V_LJ(r) = 4 epsilon [ (sigma / r)^12 - (sigma / r)^6 ],

where r is the distance between the two particles, epsilon is the depth of the potential well (the
"dispersion energy") and sigma is the distance at which the pair energy is zero (the "size" of the
particle). The r^-12 term describes Pauli repulsion at short range, and the r^-6 term describes the
attraction from induced dipoles at longer range. The minimum of the potential lies at
r_min = 2^(1/6) sigma, where V = -epsilon and the force F = -dV/dr is zero.

For a group of atoms, the energy of a single atom is the sum of its pair energies with every other
atom, and the total energy of the system is the sum over all distinct pairs (each pair counted once).
""",
    {"title": "On the determination of molecular fields. II. From the equation of state of a gas",
     "authors": "J. E. Lennard-Jones", "year": 1924, "venue": "Proc. R. Soc. Lond. A 106:463-477",
     "url": "https://doi.org/10.1098/rspa.1924.0082", "section": "12-6 pair potential (modern form)",
     "verified": "Formula and test inputs from SciCode problem 47 (HF SciCode1/SciCode, dev split, gold code available)."},
    ["V(r) = 4 eps [(s/r)^12 - (s/r)^6]", "F(r) = -dV/dr = 24 eps/r [2 (s/r)^12 - (s/r)^6]",
     "r_min = 2^(1/6) s, V(r_min) = -eps", "U_total = sum_{i<j} V(r_ij)"],
    [
        {"label": "SciCode 47.1 test 1", "params": {"r": 0.5, "sigma": 1, "epsilon": 3}, "expected": {"V": lj(0.5, 1, 3)},
         "tol": 1e-6},
        {"label": "SciCode 47.1 test 2", "params": {"r": 1.1, "sigma": 2, "epsilon": 1}, "expected": {"V": lj(1.1, 2, 1)},
         "tol": 1e-6},
        {"label": "SciCode 47.1 test 3 (r = sigma)", "params": {"r": 5.0, "sigma": 5, "epsilon": 3}, "expected": {"V": 0.0},
         "tol": 1e-9},
        {"label": "minimum", "params": {"sigma": 1, "epsilon": 1}, "expected": {"r_min": 2 ** (1 / 6), "V_min": -1.0,
         "F_at_r_min": 0.0}, "tol": 1e-9},
        {"label": "SciCode 47.2 test 1 (two atoms 1.2 apart)", "params": {"positions": [[0, 0, 0], [1.2, 0, 0]], "i": 0,
         "sigma": 1, "epsilon": 1}, "expected": {"U_i": lj(1.2, 1, 1)}, "tol": 1e-6},
        {"label": "SciCode 47.2 test 2", "params": {"r_i": [0.5, 0.7, 0.3], "i": 1,
         "positions": [[0.1, 1.5, 0.3], [0.8, 0.0, 0.0], [1.5, 1.5, 0.0], [2.5, 2.5, 0.0]], "sigma": 1, "epsilon": 2},
         "expected": {"U_i": sum(lj(dist([0.5, 0.7, 0.3], p), 1, 2) for k, p in
                                 enumerate([[0.1, 1.5, 0.3], [0.8, 0.0, 0.0], [1.5, 1.5, 0.0], [2.5, 2.5, 0.0]]) if k != 1)},
         "tol": 1e-6},
        {"label": "three atoms on a line at 0, 1.12, 2.24 (sigma=eps=1)", "params": {"x": [0, 1.12, 2.24]},
         "expected": {"U_total": 2 * lj(1.12, 1, 1) + lj(2.24, 1, 1)}, "tol": 1e-6},
    ],
    [
        "V(r) = 4 epsilon [(sigma/r)^12 - (sigma/r)^6]",
        "sigma is where V = 0; epsilon is the well depth",
        "Minimum at r = 2^(1/6) sigma (about 1.122 sigma) with V = -epsilon",
        "The r^-12 term is repulsive (short range), the r^-6 term attractive (van der Waals)",
        "Force is zero at the minimum, repulsive inside it and attractive outside",
        "Total energy of a cluster sums each pair once",
    ],
    [
        "Minimum placed at r = sigma",
        "Double-counting pairs in the total energy",
        "Force sign reversed (F = +dV/dr)",
        "Plot y-range dominated by the r^-12 blow-up so the well is invisible",
        "Allowing r = 0 (division by zero)",
    ],
    [
        q("At what separation is the Lennard-Jones energy zero?", ["r = 0", "r = sigma", "r = 2^(1/6) sigma", "r = epsilon"],
          "B", 1),
        q("Where is the potential minimum?", ["r = sigma", "r = 2^(1/6) sigma", "r = 2 sigma", "r = sigma/2"], "B", 2),
        q("What is the energy at the minimum?", ["0", "-epsilon", "-4 epsilon", "+epsilon"], "B", 2),
        q("Which term represents short-range repulsion?", ["(sigma/r)^6", "(sigma/r)^12", "epsilon", "4"], "B", 3),
        q("How is the total energy of a small cluster computed?",
          ["Sum of V over each distinct pair once", "Product of pair energies", "Only nearest pair", "V at the centre"], "A", 5),
    ],
    invariants=["V(sigma) == 0", "V(r_min) == -epsilon", "F(r_min) == 0"],
    benchmark={"name": "SciCode", "license": "Apache-2.0", "problem_id": "47", "steps": ["47.1", "47.2", "47.3"],
               "split": "dev (gold solution public)", "test_inputs": "copied verbatim into settings above",
               "targets": "computed here with the gold-code formula (h5 targets not downloaded)"},
)

# Reciprocal lattice ---------------------------------------------------------------------------------
def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def recip(*vs):
    if len(vs) == 1:
        a = vs[0]
        n2 = dot(a, a)
        return [[2 * math.pi * x / n2 for x in a]]
    if len(vs) == 2:
        a1, a2 = vs
        a3 = cross(a1, a2)
    else:
        a1, a2, a3 = vs
    den = dot(a1, cross(a2, a3))
    b = [[2 * math.pi * x / den for x in cross(a2, a3)], [2 * math.pi * x / den for x in cross(a3, a1)],
         [2 * math.pi * x / den for x in cross(a1, a2)]]
    return b[:len(vs)]


case(
    "bench_reciprocal_lattice",
    "https://huggingface.co/datasets/SciCode1/SciCode",
    "Reciprocal lattice vectors of a crystal (SciCode problem 38; the standard construction from solid-state "
    "physics). Explain how reciprocal vectors b_i = 2 pi (a_j x a_k) / (a_1 . (a_2 x a_3)) are built from "
    "real-space lattice vectors and why a_i . b_j = 2 pi delta_ij. Let the learner edit two or three lattice "
    "vectors (and the lattice angle in 2D). Show the real and reciprocal vectors, the cell volume and the "
    "matrix of dot products a_i . b_j. Guide them through a square lattice and a stretched or skewed one. "
    "Check that a_i . b_j equals 2 pi on the diagonal and 0 elsewhere, and that a longer real-space vector "
    "gives a shorter reciprocal vector.",
    "Materials science and engineering junior who knows vectors and the cross product, new to crystallography.",
    """
Benchmark provenance: SciCode problem 38 "Reciprocal_lattice_vector", steps 38.1-38.3, Apache-2.0,
https://huggingface.co/datasets/SciCode1/SciCode (dev split). Paraphrase of the scientist-written
background.

For real-space lattice vectors a1, a2, a3 of a crystal, the reciprocal lattice vectors are

    b1 = 2 pi (a2 x a3) / (a1 . (a2 x a3)),
    b2 = 2 pi (a3 x a1) / (a1 . (a2 x a3)),
    b3 = 2 pi (a1 x a2) / (a1 . (a2 x a3)),

where x is the cross product, computed component-wise as
a x b = (a2 b3 - a3 b2, a3 b1 - a1 b3, a1 b2 - a2 b1). The denominator a1 . (a2 x a3) is the volume of
the unit cell. For a one-dimensional lattice a1 = a x_hat, the reciprocal vector is b1 = (2 pi / a) x_hat.
For a two-dimensional lattice, the third vector a3 is taken perpendicular to the plane (for example
a1 x a2) and only b1 and b2 are kept. By construction a_i . b_j = 2 pi when i = j and 0 otherwise, so
each reciprocal vector is perpendicular to the other two real-space vectors; reciprocal lengths scale
as the inverse of real-space lengths.
""",
    {"title": "SciCode problem 38: Reciprocal lattice vector", "authors": "SciCode team (Tian et al. 2024)",
     "year": 2024, "venue": "SciCode benchmark, NeurIPS 2024 D&B (arXiv:2407.13168)",
     "url": "https://huggingface.co/datasets/SciCode1/SciCode", "section": "problem 38, steps 38.1-38.3",
     "verified": "Problem text and gold code read from problems_dev.jsonl on 2026-10-03."},
    ["b_i = 2 pi (a_j x a_k) / (a1 . (a2 x a3))", "a_i . b_j = 2 pi delta_ij", "1D: b = 2 pi a / |a|^2",
     "2D: a3 = a1 x a2, keep b1, b2"],
    [
        {"label": "SciCode 38.2 test 1", "params": {"a1": [3, 4, 5], "a2": [4, 3, 2], "a3": [1, 0, 2]},
         "expected": {"b": recip([3, 4, 5], [4, 3, 2], [1, 0, 2]), "cell_volume": dot([3, 4, 5], cross([4, 3, 2], [1, 0, 2]))},
         "tol": 1e-6},
        {"label": "SciCode 38.3 test 2", "params": {"a1": [1, 1, 0], "a2": [1, -1, 2], "a3": [1, 3, 5]},
         "expected": {"b": recip([1, 1, 0], [1, -1, 2], [1, 3, 5])}, "tol": 1e-6},
        {"label": "SciCode 38.3 test 3 (2D)", "params": {"a1": [1, 4, 0], "a2": [2, -1, 0]},
         "expected": {"b": recip([1, 4, 0], [2, -1, 0])}, "tol": 1e-6},
        {"label": "SciCode 38.3 test 4 (1D)", "params": {"a1": [1, 1, 5]}, "expected": {"b": recip([1, 1, 5])}, "tol": 1e-6},
        {"label": "square lattice a = 2", "params": {"a1": [2, 0, 0], "a2": [0, 2, 0]},
         "expected": {"b": recip([2, 0, 0], [0, 2, 0]), "length_b": math.pi}, "tol": 1e-9},
    ],
    [
        "b1 = 2 pi (a2 x a3)/(a1 . (a2 x a3)) and cyclic permutations",
        "a_i . b_j = 2 pi if i = j, otherwise 0",
        "The denominator a1 . (a2 x a3) is the unit-cell volume",
        "Longer real-space vectors give shorter reciprocal vectors (inverse scaling)",
        "In 2D, use a third vector perpendicular to the plane; in 1D, b = 2 pi / a",
        "Each b_i is perpendicular to the two real-space vectors it is built from",
    ],
    [
        "Omitting the factor 2 pi (crystallographer's convention) without saying so",
        "Wrong cyclic order (a3 x a2) giving a sign flip",
        "Dividing by |a2 x a3| instead of the triple product",
        "Degenerate (coplanar) vectors producing Infinity without a warning",
        "Claiming b_i is parallel to a_i in a skewed lattice",
    ],
    [
        q("What is a_1 . b_1 by construction?", ["0", "1", "2 pi", "The cell volume"], "C", 1),
        q("What is a_1 . b_2?", ["0", "2 pi", "1", "pi"], "A", 1),
        q("What does a1 . (a2 x a3) represent?", ["The unit-cell volume", "The angle between vectors",
          "The reciprocal length", "Zero always"], "A", 2),
        q("If a real-space lattice vector doubles in length (square lattice), the matching reciprocal vector:",
          ["Doubles", "Halves", "Stays the same", "Rotates 90 degrees only"], "B", 3),
        q("Which formula gives b1?", ["2 pi (a2 x a3)/(a1 . (a2 x a3))", "a1 / |a1|", "2 pi a1", "a2 x a3"], "A", 0),
    ],
    invariants=["a_i . b_j == 2 pi delta_ij"],
    benchmark={"name": "SciCode", "license": "Apache-2.0", "problem_id": "38", "steps": ["38.1", "38.2", "38.3"],
               "split": "dev (gold solution public)", "test_inputs": "copied verbatim",
               "targets": "computed with the gold-code formula; cross-checked by executing the gold code"},
)

# PN junction ------------------------------------------------------------------------------------------
case(
    "bench_pn_junction",
    "https://huggingface.co/datasets/SciCode1/SciCode",
    "Abrupt p-n junction electrostatics in the depletion approximation (SciCode problem 34). Explain how "
    "doping sets the built-in potential and how charge neutrality splits the depletion region between the "
    "p and n sides. Let the learner set the acceptor and donor concentrations, the intrinsic carrier "
    "density and the relative permittivity. Show the built-in potential, x_n, x_p, the charge density, "
    "the triangular electric field and the parabolic potential (conduction band) across the junction. "
    "Guide them through symmetric doping and a one-sided junction. Check that N_a x_p = N_d x_n, that the "
    "potential drop equals the built-in potential, and that the more lightly doped side holds most of the "
    "depletion width.",
    "Electrical engineering junior in a first semiconductor devices course.",
    """
Benchmark provenance: SciCode problem 34 "PN_diode_band_diagram", steps 34.1-34.3, Apache-2.0,
https://huggingface.co/datasets/SciCode1/SciCode (test split). Paraphrase of the scientist-written
background.

Assume constant doping on each side (acceptors N_a on the p side, donors N_d on the n side), Boltzmann
statistics, fully ionised dopants and room temperature (thermal voltage kT/q = 0.0259 V). The
built-in biases of the two sides relative to the intrinsic level are
    phi_p = kT ln(N_a / n_i),   phi_n = kT ln(N_d / n_i),
and the total built-in potential is phi_i = phi_p + phi_n. Poisson's equation says the gradient of the
electric field equals the charge density divided by the permittivity epsilon = epsilon_r epsilon_0
(epsilon_0 = 8.854e-14 F/cm, q = 1.6e-19 C). With constant doping the field is piecewise linear and the
depletion width is
    x_d = [ (2 epsilon phi_i / q) (N_a + N_d) / (N_a N_d) ]^(1/2),
split so that the charges balance: N_a x_p = N_d x_n. Integrating the field gives a potential that is
quadratic on each side; the conduction-band potential is set to 0 V at the edge of the depletion region
on the p side and changes by phi_i across the junction (output on a 0.1 nm grid in the benchmark).
""",
    {"title": "SciCode problem 34: PN diode band diagram", "authors": "SciCode team (Tian et al. 2024)", "year": 2024,
     "venue": "SciCode benchmark (arXiv:2407.13168)", "url": "https://huggingface.co/datasets/SciCode1/SciCode",
     "section": "problem 34, steps 34.1-34.3",
     "verified": "Problem text and test inputs read from problems_test.jsonl on 2026-10-03 (no public gold code; "
                 "targets computed here from the stated formulas)."},
    ["phi_p = kT ln(Na/ni); phi_n = kT ln(Nd/ni); V_bi = phi_p + phi_n (kT = 0.0259 V)",
     "x_d = sqrt(2 eps V_bi / q (Na+Nd)/(Na Nd)); x_n = x_d Na/(Na+Nd); x_p = x_d Nd/(Na+Nd)",
     "E_max = q Nd x_n / eps"],
    [
        {"label": "SciCode 34.1/34.2 test 1", "params": {"Na": 2e17, "Nd": 3e17, "ni": 1e12, "er": 15},
         "expected": pn_junction(2e17, 3e17, 1e12, 15), "tol": 1e-3},
        {"label": "SciCode 34.1/34.2 test 2", "params": {"Na": 1e17, "Nd": 2e17, "ni": 1e12, "er": 15},
         "expected": pn_junction(1e17, 2e17, 1e12, 15), "tol": 1e-3},
        {"label": "SciCode 34.3 test 1 (symmetric -> xn == xp)", "params": {"Na": 2e17, "Nd": 2e17, "ni": 1e11, "er": 15},
         "expected": dict(pn_junction(2e17, 2e17, 1e11, 15), xn_equals_xp=True), "tol": 1e-3},
        {"label": "one-sided (Si-like)", "params": {"Na": 1e18, "Nd": 1e15, "ni": 1e10, "er": 11.7},
         "expected": pn_junction(1e18, 1e15, 1e10, 11.7), "tol": 1e-3},
    ],
    [
        "Built-in potential V_bi = (kT/q) ln(Na Nd / ni^2)",
        "Charge neutrality: Na x_p = Nd x_n",
        "Depletion width x_d = sqrt(2 eps V_bi (Na+Nd)/(q Na Nd))",
        "The more lightly doped side holds most of the depletion width",
        "Field is linear (triangular) and potential is quadratic in the depletion region",
        "Assumptions: abrupt junction, full ionisation, Boltzmann statistics, depletion approximation",
    ],
    [
        "Putting the wider depletion region on the heavily doped side",
        "Using log10 instead of natural log",
        "Unit slips between cm and m (eps0 = 8.854e-12 F/m vs 8.854e-14 F/cm)",
        "Linear instead of parabolic potential",
        "Field peak not at the metallurgical junction",
    ],
    [
        q("Which side of a one-sided p+n junction holds most of the depletion width?",
          ["The heavily doped side", "The lightly doped side", "They are always equal", "Neither: it is outside"], "B", 3),
        q("Which relation expresses charge neutrality in the depletion region?",
          ["Na x_n = Nd x_p", "Na x_p = Nd x_n", "x_n = x_p always", "Na = Nd"], "B", 1),
        q("The built-in potential depends on doping as:",
          ["(kT/q) ln(Na Nd / ni^2)", "q Na Nd", "kT (Na + Nd)", "ni^2 / (Na Nd)"], "A", 0),
        q("What shape is the electric field across an abrupt junction?",
          ["Constant", "Triangular (piecewise linear)", "Exponential", "Sinusoidal"], "B", 4),
        q("What shape is the potential across the depletion region?",
          ["Linear", "Piecewise quadratic", "Step function", "Logarithmic"], "B", 4),
    ],
    invariants=["Na*xp == Nd*xn", "potential drop == V_bi", "field max at x=0"],
    benchmark={"name": "SciCode", "license": "Apache-2.0", "problem_id": "34", "steps": ["34.1", "34.2", "34.3"],
               "split": "test (no public gold code)", "test_inputs": "copied verbatim (34.1/34.2 tests 1-2, 34.3 test 1)",
               "targets": "computed here from the background formulas; original h5 targets not downloaded",
               "note": "SciCode docstrings label N_a/N_d inconsistently; we use Na = acceptors (p side)."},
)

# DBR ----------------------------------------------------------------------------------------------------
dbr_set = []
for lam, n1, n2, N in [(980, 3.52, 2.95, 100), (1000, 3.5, 3.0, 10), (1500, 3.52, 2.95, 20), (800, 3.52, 2.95, 20),
                       (980, 3.52, 2.95, 10), (980, 3.52, 2.95, 20)]:
    res = dbr_scicode(lam, 980, n1, n2, N)
    exp = {"phase_phi": res["phi"], "half_trace_(A+D)/2": res["half_trace"], "R": res["R"]}
    if lam == 980:
        exp["R_closed_form_tanh2(N ln(n1/n2))"] = dbr_peak_closed_form(n1, n2, N)
    dbr_set.append({"label": f"lambda={lam} nm, n1={n1}, n2={n2}, N={N}",
                    "params": {"lambda_in_nm": lam, "lambda_b_nm": 980, "n1": n1, "n2": n2, "N": N},
                    "expected": exp, "tol": 1e-4})
case(
    "bench_dbr",
    "https://huggingface.co/datasets/SciCode1/SciCode",
    "Reflection spectrum of a distributed Bragg reflector made of quarter-wave GaAs/AlAs layers (SciCode problem "
    "39, transfer-matrix method for a periodic stack). Explain why many weakly reflecting interfaces add up "
    "to near-total reflection at the Bragg wavelength. Let the learner set the two refractive indices, the "
    "number of layer pairs and the design wavelength, and sweep the incident wavelength. Show the phase per "
    "layer, the trace (A + D)/2 of the unit-cell matrix, and the reflectance spectrum with the stop band. "
    "Guide them through few versus many pairs and through a small versus large index contrast. Check that "
    "reflectance stays between 0 and 1, that inside the stop band |(A + D)/2| > 1, and that at the Bragg "
    "wavelength reflectance approaches 1 as the number of pairs grows.",
    "Electrical engineering senior in a photonics elective; knows complex numbers and 2x2 matrices.",
    """
Benchmark provenance: SciCode problem 39 "Reflection_spectra_for_a_Distributed_Bragg_Reflector",
steps 39.1-39.3, Apache-2.0, https://huggingface.co/datasets/SciCode1/SciCode (test split). Paraphrase
of the scientist-written background (matrix method for plane-wave reflection from a DBR).

A vertical-cavity laser designed for emission at lambda_b uses a mirror of N pairs of quarter-wave
layers with refractive indices n1 (GaAs) and n2 (AlAs). For incident wavelength lambda the phase shift
in each quarter-wave layer is phi = pi lambda_b / (2 lambda). The propagation matrix of one period is
M = [[A, B], [C, D]] with
    A = (n1 + n2)^2 / (4 n1 n2) e^{-2 i phi} - (n1 - n2)^2 / (4 n1 n2),
    B = (n2^2 - n1^2) / (4 n1 n2) (1 - e^{2 i phi}),   C = conj(B),   D = conj(A).
A pseudo-angle theta is defined by cos(theta) = (A + D) / 2. When |(A + D)/2| > 1 there is no real
solution: theta = m pi + i alpha (m = 1 is assumed), which is the stop band of the mirror. The
reflectance of N periods is
    R = |C|^2 / ( |C|^2 + |sin(theta) / sin(N theta)|^2 ),
or, inside the stop band, the same expression with sinh(alpha) / sinh(N alpha).
""",
    {"title": "SciCode problem 39: Reflection spectra for a Distributed Bragg Reflector", "authors": "SciCode team",
     "year": 2024, "venue": "SciCode benchmark (arXiv:2407.13168)", "url": "https://huggingface.co/datasets/SciCode1/SciCode",
     "section": "problem 39, steps 39.1-39.3",
     "verified": "Problem text and test inputs read from problems_test.jsonl on 2026-10-03; targets computed here "
                 "and cross-checked at lambda_b against the closed form tanh^2(N ln(n1/n2))."},
    ["phi = pi lambda_b/(2 lambda)", "A, B, C, D as in excerpt; cos theta = (A+D)/2",
     "R = |C|^2/(|C|^2 + |sin theta/sin N theta|^2) (sinh form in stop band)",
     "at lambda_b: R = tanh^2(N ln(n1/n2))"],
    dbr_set,
    [
        "Each layer is a quarter wavelength thick at the design (Bragg) wavelength",
        "Reflections from many interfaces add in phase at the Bragg wavelength",
        "Stop band where |(A+D)/2| > 1 and the Bloch phase becomes complex",
        "Peak reflectance rises toward 1 as the number of pairs N increases",
        "Larger index contrast gives a wider stop band and fewer pairs needed",
        "R = |C|^2 / (|C|^2 + |sin theta / sin N theta|^2)",
    ],
    [
        "Reflectance above 1 or negative",
        "Using sin instead of sinh inside the stop band (NaN or wrong R)",
        "Phase defined as 2 pi lambda_b / lambda instead of pi lambda_b / (2 lambda)",
        "Stop band centred at the wrong wavelength",
        "Claiming R reaches exactly 1 for finite N",
    ],
    [
        q("How thick is each DBR layer?", ["A quarter of the design wavelength (in the material)", "One full wavelength",
          "Arbitrary", "Half the incident wavelength in air"], "A", 0),
        q("What happens to peak reflectance as the number of layer pairs grows?",
          ["It falls", "It approaches 1", "It oscillates around 0.5", "It is unchanged"], "B", 3),
        q("The stop band corresponds to:", ["|(A+D)/2| < 1", "|(A+D)/2| > 1", "A = D", "B = 0"], "B", 2),
        q("A larger refractive-index contrast between the two layers gives:",
          ["A narrower stop band", "A wider stop band", "No reflection", "Only transmission"], "B", 4),
        q("Why do many weak reflections give strong total reflection at the Bragg wavelength?",
          ["They add in phase", "They cancel", "Absorption increases", "The layers are metallic"], "A", 1),
    ],
    invariants=["0 <= R <= 1", "|(A+D)/2|>1 in stop band", "R(lambda_b) increasing in N"],
    benchmark={"name": "SciCode", "license": "Apache-2.0", "problem_id": "39", "steps": ["39.1", "39.2", "39.3"],
               "split": "test (no public gold code)", "test_inputs": "39.3 tests copied verbatim",
               "targets": "computed here from the background formulas; original h5 targets not downloaded"},
)

# Sigma-delta (TheoremExplainBench theorem_207) ------------------------------------------------------------
sd = {x: sigma_delta(x, 16) for x in (0.5, 0.0, -0.25)}
sd64 = sigma_delta(0.3, 64)
case(
    "bench_sigma_delta",
    "https://doi.org/10.1109/IRET-SET.1962.5008839",
    "Inose, Yasuda and Murakami (1962), A Telemetering System by Code Modulation: Delta-Sigma Modulation. "
    "Explain how a first-order delta-sigma modulator turns a slowly varying input into a one-bit stream whose "
    "average equals the input. Let the learner set a constant input between -1 and 1 and the number of "
    "samples, and step through the loop. Show, at each step, the integrator state, the comparator output "
    "bit and the running average of the bits. Guide them through input 0 and input 0.5. Check that the "
    "running average converges to the input with error at most about 2/N and that the integrator stays bounded.",
    "Electrical engineering junior who knows feedback loops and binary numbers, new to data converters.",
    """
Benchmark provenance: TheoremExplainBench theorem_207 "Sigma-Delta Modulation" (comp_sci / Digital Signal
Processing, Hard), MIT licence, https://huggingface.co/datasets/TIGER-Lab/TheoremExplainBench. The
benchmark's description: a sigma-delta modulator converts a voltage into a high-frequency one-bit
digital bitstream using oversampling and noise shaping. Primary source: H. Inose, Y. Yasuda and
J. Murakami, IRE Trans. Space Electronics and Telemetry SET-8 (1962) 204-209. Paraphrased; not
checked against the paywalled original.

A first-order delta-sigma modulator is a feedback loop with an integrator (the "sigma", a running sum)
and a one-bit quantiser (comparator). At each clock tick the loop subtracts the previous output bit
(the "delta") from the input, adds the difference to the integrator, and outputs +1 if the integrator
is non-negative and -1 otherwise:
    v[n] = v[n-1] + x[n] - y[n-1],    y[n] = +1 if v[n] >= 0 else -1.
Because the integrator stays bounded, the average of the output bits over N ticks differs from the
average input by at most a bounded amount divided by N, so the density of +1s encodes the input
value. Running the loop much faster than the signal changes (oversampling) and low-pass filtering the
bit stream recovers the signal; the feedback pushes quantisation noise towards high frequencies
(noise shaping).
""",
    {"title": "A Telemetering System by Code Modulation - Delta-Sigma Modulation", "authors": "H. Inose, Y. Yasuda, "
     "J. Murakami", "year": 1962, "venue": "IRE Trans. Space Electronics and Telemetry SET-8(3):204-209",
     "url": "https://doi.org/10.1109/IRET-SET.1962.5008839", "section": "first-order modulator loop",
     "verified": "Theorem/description from TheoremExplainBench (HF datasets-server) 2026-10-03; primary paywalled."},
    ["v[n] = v[n-1] + x - y[n-1]; y[n] = sign(v[n]) (>=0 -> +1); v[-1] = y[-1] = 0",
     "mean(y[0..N-1]) -> x, |mean - x| <= ~2/N"],
    [
        {"label": f"x = {x}, N = 16", "params": {"x": x, "N": 16, "v_init": 0, "y_init": 0},
         "expected": {"bits": sd[x][0], "mean": sd[x][1], "abs_error": abs(sd[x][1] - x)}, "tol": 1e-9,
         "note": "exact bit pattern depends on initial state/comparator convention; mean within 2/N is the robust check"}
        for x in (0.5, 0.0, -0.25)
    ] + [{"label": "x = 0.3, N = 64", "params": {"x": 0.3, "N": 64}, "expected": {"mean": sd64[1],
          "abs_error": abs(sd64[1] - 0.3), "bound_2_over_N": 2 / 64}, "tol": 1e-9}],
    [
        "Loop = integrator (sum) + one-bit quantiser with the output fed back and subtracted (delta)",
        "The average (density of +1s) of the bitstream equals the input value",
        "Error of the N-sample average shrinks like 1/N because the integrator is bounded",
        "Oversampling: the loop runs much faster than the signal changes",
        "Noise shaping pushes quantisation noise to high frequencies, removed by a low-pass filter",
        "Input 0 gives an alternating +1/-1 pattern",
    ],
    [
        "Feeding back the integrator instead of the output bit",
        "Running average not converging (integrator unbounded) because of a sign error",
        "Allowing inputs outside [-1, 1] without noting the loop overloads",
        "Claiming a single bit carries the input value",
        "Confusing delta-sigma with plain delta modulation",
    ],
    [
        q("What does the average of a delta-sigma bitstream represent?",
          ["The input value", "The clock rate", "Always zero", "The integrator maximum"], "A", 1),
        q("What is fed back and subtracted from the input?", ["The integrator state", "The output bit",
          "The clock", "Nothing"], "B", 0),
        q("For input 0, the output pattern is:", ["All +1", "All -1", "Alternating +1 and -1", "Random"], "C", 5),
        q("How does the error of the N-sample average behave?", ["Grows with N", "Shrinks about like 1/N",
          "Constant", "Exponential growth"], "B", 2),
        q("Where does noise shaping push quantisation noise?", ["To low frequencies", "To high frequencies",
          "Into the input", "It removes it entirely"], "B", 4),
    ],
    invariants=["|mean(bits) - x| <= 2/N", "integrator bounded (|v| <= 2 for |x| <= 1)"],
    benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_207", "subject": "comp_sci",
               "subfield": "Digital Signal Processing", "difficulty": "Hard",
               "targets": "computed here with the stated loop equations"},
)

# Van der Waals (TheoremExplainBench theorem_190) -----------------------------------------------------------
Rg = 0.083145  # L bar / (mol K)
aC, bC = 3.640, 0.04267  # CO2, L^2 bar/mol^2 and L/mol


def vdw_P(T, Vm, a=aC, b=bC):
    return Rg * T / (Vm - b) - a / Vm ** 2


case(
    "bench_van_der_waals",
    "https://www.nobelprize.org/uploads/2018/06/waals-lecture.pdf",
    "Van der Waals equation of state, (P + a/V_m^2)(V_m - b) = R T, as described in his 1910 Nobel lecture "
    "The Equation of State for Gases and Liquids. Explain how the two corrections (attraction a and excluded "
    "volume b) bend the ideal-gas isotherms and create a critical point. Let the learner set temperature, "
    "molar volume and the constants a and b (default carbon dioxide). Show pressure from the van der Waals "
    "and ideal-gas laws, a family of isotherms, and the critical point. Guide them through a temperature "
    "above the critical temperature and one below it, where the isotherm develops a loop. Check that "
    "T_c = 8a/(27 R b), P_c = a/(27 b^2), V_c = 3b, and that at large volume the equation approaches the ideal gas.",
    "Chemical engineering sophomore in a thermodynamics course who knows the ideal gas law.",
    """
Benchmark provenance: TheoremExplainBench theorem_190 "Van der Waals Equation" (physics / Thermodynamics,
Hard), MIT licence, https://huggingface.co/datasets/TIGER-Lab/TheoremExplainBench. The benchmark's
description: an equation of state for real gases relating pressure, temperature and molar volume.
Primary source: J. D. van der Waals, "The Equation of State for Gases and Liquids", Nobel Lecture,
12 December 1910. Paraphrased; checked against the lecture PDF text on 2026-10-03.

Van der Waals corrected the ideal-gas law P V_m = R T for two effects. Molecules attract one another,
which lowers the pressure they exert on the walls; this is represented by an added "internal pressure"
a / V_m^2. Molecules also occupy volume themselves, so the free volume is V_m - b. The resulting
equation of state is

    (P + a / V_m^2)(V_m - b) = R T,   i.e.  P = R T / (V_m - b) - a / V_m^2.

Below a critical temperature the isotherms have a maximum and a minimum (a "loop"), which signals
the coexistence of liquid and gas; above it they decrease monotonically. At the critical point the
isotherm has an inflection with zero slope, giving T_c = 8a / (27 R b), P_c = a / (27 b^2) and
V_c = 3b, so the model predicts P_c V_c / (R T_c) = 3/8 for every gas. At large molar volume both
corrections become negligible and the ideal-gas law is recovered. In the lecture van der Waals stresses
that b is not truly constant (it shrinks at small volumes), so measured critical values of real gases
deviate from these simple predictions.
""",
    {"title": "The Equation of State for Gases and Liquids (Nobel Lecture)", "authors": "J. D. van der Waals",
     "year": 1910, "venue": "Nobel Lecture, 12 Dec 1910", "url": "https://www.nobelprize.org/uploads/2018/06/waals-lecture.pdf",
     "section": "equation of state and critical point",
     "verified": "Theorem/description from TheoremExplainBench 2026-10-03; Nobel lecture PDF text extracted and "
                 "critical-point discussion (pk = a/(27 b^2), pv/RT vs 3/8, variable b) confirmed."},
    ["P = R T/(Vm - b) - a/Vm^2 (R = 0.083145 L bar/(mol K))", "Tc = 8a/(27 R b); Pc = a/(27 b^2); Vc = 3b",
     "Zc = Pc Vc/(R Tc) = 3/8", "CO2: a = 3.640 L^2 bar/mol^2, b = 0.04267 L/mol"],
    [
        {"label": "CO2 critical point", "params": {"a": aC, "b": bC}, "expected": {"Tc_K": 8 * aC / (27 * Rg * bC),
         "Pc_bar": aC / (27 * bC ** 2), "Vc_L_per_mol": 3 * bC, "Zc": 0.375}, "tol": 1e-3},
        {"label": "CO2 at 300 K, 0.5 L/mol", "params": {"T": 300, "Vm": 0.5, "a": aC, "b": bC},
         "expected": {"P_vdw_bar": vdw_P(300, 0.5), "P_ideal_bar": Rg * 300 / 0.5}, "tol": 1e-3},
        {"label": "CO2 at 350 K, 0.2 L/mol", "params": {"T": 350, "Vm": 0.2, "a": aC, "b": bC},
         "expected": {"P_vdw_bar": vdw_P(350, 0.2), "P_ideal_bar": Rg * 350 / 0.2}, "tol": 1e-3},
        {"label": "large volume -> ideal", "params": {"T": 300, "Vm": 50.0, "a": aC, "b": bC},
         "expected": {"P_vdw_bar": vdw_P(300, 50.0), "P_ideal_bar": Rg * 300 / 50.0}, "tol": 1e-3},
    ],
    [
        "Equation (P + a/Vm^2)(Vm - b) = R T",
        "a accounts for intermolecular attraction (lowers pressure); b for the molecules' own volume",
        "Critical point Tc = 8a/(27 R b), Pc = a/(27 b^2), Vc = 3b",
        "Below Tc isotherms show a loop (liquid-gas coexistence); above Tc they are monotonic",
        "At large molar volume the equation reduces to the ideal gas law",
        "Critical compressibility factor Zc = 3/8 for every van der Waals gas (real gases deviate; b is not truly constant)",
    ],
    [
        "Using total volume V instead of molar volume without n",
        "Sign of the attraction term reversed (P higher than ideal at moderate density)",
        "R in J/(mol K) with a, b in L-bar units",
        "Plotting the loop as a physical stable state without comment (Maxwell construction)",
        "Division by zero or negative pressure for Vm <= b not handled",
    ],
    [
        q("What does the constant b represent?", ["Attraction between molecules", "The volume excluded by the molecules "
          "themselves", "The gas constant", "Temperature"], "B", 1),
        q("What is the critical temperature in terms of a, b and R?", ["a/(27 b^2)", "8a/(27 R b)", "3b", "R b / a"], "B", 2),
        q("Above the critical temperature, the isotherms are:", ["Looped", "Monotonically decreasing", "Constant",
          "Undefined"], "B", 3),
        q("At very large molar volume, the van der Waals equation:", ["Diverges", "Reduces to the ideal gas law",
          "Gives zero pressure", "Gives negative pressure"], "B", 4),
        q("What is the critical compressibility factor Pc Vc/(R Tc)?", ["1", "3/8", "8/27", "1/27"], "B", 5),
    ],
    invariants=["Vm > b", "P_vdw -> P_ideal as Vm -> inf", "dP/dV = 0 and d2P/dV2 = 0 at critical point"],
    benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_190", "subject": "physics",
               "subfield": "Thermodynamics", "difficulty": "Hard", "targets": "computed here"},
)

# Langmuir (TheoremExplainBench theorem_233) --------------------------------------------------------------
lang = lambda P, K: K * P / (1 + K * P)
case(
    "bench_langmuir",
    "https://doi.org/10.1021/ja02242a004",
    "Langmuir (1918), The Adsorption of Gases on Plane Surfaces of Glass, Mica and Platinum: the monolayer "
    "adsorption isotherm theta = K P / (1 + K P). Explain how a balance between adsorption onto empty sites "
    "and desorption from occupied sites gives a saturating coverage. Let the learner set pressure and the "
    "equilibrium constant (or the adsorption and desorption rates). Show the fraction of covered sites, the "
    "adsorption and desorption rates at the current state, and the isotherm curve. Guide them through low "
    "pressure (nearly linear) and high pressure (saturation). Check that coverage is half when K P = 1, "
    "that it never exceeds 1, and that the two rates are equal at the computed coverage.",
    "Chemical and process engineering undergraduate who knows equilibrium constants but not surface science.",
    """
Benchmark provenance: TheoremExplainBench theorem_233 "Langmuir Adsorption Isotherm" (chemistry / Physical
Chemistry, Hard), MIT licence, https://huggingface.co/datasets/TIGER-Lab/TheoremExplainBench. The
benchmark's description: a continuous monolayer of adsorbate molecules on a homogeneous solid surface is
the conceptual basis for the model. Primary source: I. Langmuir, J. Am. Chem. Soc. 40 (1918) 1361-1403.
Paraphrased; not checked against the paywalled original.

Langmuir pictured a surface with a fixed number of equivalent adsorption sites, each holding at most
one molecule, with no interaction between adsorbed molecules (a monolayer on a homogeneous surface).
If theta is the fraction of occupied sites and P the gas pressure, molecules strike and stick to empty
sites at a rate k_a P (1 - theta), and adsorbed molecules leave at a rate k_d theta. At equilibrium
the two rates are equal, which gives

    theta = K P / (1 + K P),   with K = k_a / k_d.

At low pressure theta is approximately K P (proportional to pressure); at high pressure the surface
saturates and theta approaches 1 (a complete monolayer). Coverage is one half when P = 1/K. Rearranged,
P / theta = 1/K + P, so a plot of P/theta against P is a straight line, which is how the constants are
fitted from data.
""",
    {"title": "The Adsorption of Gases on Plane Surfaces of Glass, Mica and Platinum", "authors": "I. Langmuir",
     "year": 1918, "venue": "J. Am. Chem. Soc. 40(9):1361-1403", "url": "https://doi.org/10.1021/ja02242a004",
     "section": "monolayer isotherm derivation", "verified": "Theorem/description from TheoremExplainBench 2026-10-03."},
    ["theta = K P/(1 + K P)", "rate_ads = k_a P (1 - theta); rate_des = k_d theta; K = k_a/k_d",
     "theta = 1/2 at P = 1/K", "P/theta = 1/K + P (linearised)"],
    [
        {"label": "K P = 1", "params": {"P_bar": 2.0, "K_per_bar": 0.5}, "expected": {"theta": lang(2, 0.5)}, "tol": 1e-9},
        {"label": "high pressure", "params": {"P_bar": 10.0, "K_per_bar": 0.5}, "expected": {"theta": lang(10, 0.5)},
         "tol": 1e-6},
        {"label": "low pressure", "params": {"P_bar": 0.1, "K_per_bar": 0.5},
         "expected": {"theta": lang(0.1, 0.5), "linear_approx_KP": 0.05}, "tol": 1e-6},
        {"label": "rates balance", "params": {"P_bar": 3.0, "k_a": 2.0, "k_d": 4.0},
         "expected": {"theta": lang(3, 0.5), "rate_ads": 2 * 3 * (1 - lang(3, 0.5)), "rate_des": 4 * lang(3, 0.5)},
         "tol": 1e-6},
    ],
    [
        "Coverage theta = K P/(1 + K P)",
        "Derived from equal adsorption (k_a P (1 - theta)) and desorption (k_d theta) rates",
        "Assumptions: identical sites, one molecule per site (monolayer), no interactions between adsorbates",
        "Low pressure: theta ~ K P (linear); high pressure: theta -> 1 (saturation)",
        "Half coverage at P = 1/K",
        "Linear form P/theta = 1/K + P used to fit data",
    ],
    [
        "Coverage exceeding 1",
        "Using theta = K P without saturation",
        "Swapping K = k_d/k_a",
        "Claiming multilayer adsorption (that is BET, not Langmuir)",
        "Omitting the assumption of non-interacting identical sites",
    ],
    [
        q("What is the Langmuir isotherm?", ["theta = K P/(1 + K P)", "theta = K P", "theta = 1 - e^{-P}", "theta = P/K"], "A", 0),
        q("At what pressure is half the surface covered?", ["P = K", "P = 1/K", "P = 2/K", "P = 0"], "B", 4),
        q("What happens to coverage at very high pressure?", ["Grows without bound", "Approaches 1", "Falls to 0",
          "Equals K"], "B", 3),
        q("The isotherm comes from balancing:", ["Adsorption and desorption rates", "Temperature and pressure",
          "Gravity and buoyancy", "Two different gases"], "A", 1),
        q("Which is an assumption of the Langmuir model?", ["Multilayer adsorption", "Identical, non-interacting sites with "
          "at most one molecule each", "Strong attraction between adsorbed molecules", "Infinitely many layers"], "B", 2),
    ],
    invariants=["0 <= theta < 1", "theta monotonic in P", "k_a P (1-theta) == k_d theta"],
    benchmark={"name": "TheoremExplainBench", "license": "MIT", "uid": "theorem_233", "subject": "chemistry",
               "subfield": "Physical Chemistry", "difficulty": "Hard", "targets": "computed here"},
)

# Batch 2 (b2_*, pv_*) lives in cases_b2.py to keep this file reviewable --------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cases_b2  # noqa: E402

cases_b2.register(case, q)


# ----------------------------------------------------------------------------------------------
def write_all(check_only=False):
    for c in CASES:
        words = len(c["excerpt"].split())
        assert words <= 300, f"{c['slug']}: excerpt has {words} words (> 300)"
        assert len(c["quiz"]) == 5, c["slug"]
        for key in ("source_url", "focus", "audience"):
            assert isinstance(c["case"][key], str) and c["case"][key].strip(), (c["slug"], key)
        ref = {"case": c["slug"], "dev_only": DEV_NOTE, "source": c["source"], "formulas": c["formulas"],
               "settings": r6(c["settings"]), "invariants": c["invariants"], "must_mention": c["must_mention"],
               "common_errors": c["common_errors"], "quiz": c["quiz"]}
        if c["benchmark"]:
            ref["benchmark"] = c["benchmark"]
        if check_only:
            print(f"== {c['slug']}  ({words} words excerpt)")
            for s in ref["settings"]:
                print("  ", s["label"], json.dumps(s["expected"])[:300])
            continue
        d = ROOT / c["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.json").write_text(json.dumps(c["case"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (d / "excerpt.txt").write_text(c["excerpt"], encoding="utf-8")
        (d / "reference.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not check_only:
        print(f"wrote {len(CASES)} cases to {ROOT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="print reference values, write nothing")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    write_all(a.check)

# -*- coding: utf-8 -*-
"""在线学习算法层：能力/难度估计、模式风险、讲法排名、校准与收敛诊断。

模型（MAP，logit 尺度）::

    logit P(流畅通过) = theta + theta_subj[s] - d(a) - gamma*dt + retry_delta*retry
    d(a) = tier_mean[t] + delta[a]        # 部分汇聚：单样本原子不会乱飞

先验（写清楚才不会冷启动胡说）::

    theta        ~ N(0.9, 1.5^2)          能力基座
    theta_subj   ~ N(0,   0.6^2)          科目偏置
    tier_mean    ~ N(档位先验, 0.5^2)     S 难 / A 中 / B 易
    delta[a]     ~ N(上次判定修正, 0.6^2) 原子相对本档的偏离（上次 ❌ 则先验更难）
    gamma        ~ 固定 ln2/10（样本够时才自由拟合，带 N(g0, 0.05^2) 先验）
    retry_delta  ~ N(0, 0.5^2)            同一天复测的即时提升

优化：全批梯度 + Armijo 回溯线搜索，收敛判据 = 梯度无穷范数或目标相对变化。

为什么换掉「固定迭代次数的 SGD」：
  * 固定次数既可能没收敛（噪声大时来回晃），也可能白算（早就到最优点）；
  * 线搜索保证目标单调下降，于是「收没收敛」可判定、可复现、可报告；
  * 先验在拟合前一次性算好（按事件顺序回溯），既没有未来信息泄漏，
    又把每次迭代从 O(n^2) 降到 O(n)——事件流变长后差距是数量级的。

预测纪律：先预测、后作答；预测写进 预测日志.csv，答完用 walk-forward 回测校准。
"""
from __future__ import annotations

import datetime
import math
import re

from .fsutil import append_text, read_lines

GAMMA0 = math.log(2) / 10.0          # 遗忘常数：10 天半衰期
THETA0 = 0.9
SIG_THETA = 1.5
SIG_SUBJ = 0.6
SIG_TIER = 0.5
SIG_DELTA = 0.6
SIG_GAMMA = 0.05
SIG_RETRY = 0.5

TIERS = ["S", "A", "B", "?"]
TIER_PRIOR = {"S": 0.28, "A": 0.15, "B": 0.05, "?": 0.15}
HINT_SHIFT = {"": 0.0, "⬜": 0.0, "✅": -0.22, "⚠️": 0.22, "❌": 0.50}
JUDGED = ("✅", "⚠️", "❌")
Z90 = 1.645

PRED_COLS = ["日期", "时间", "原子ID", "事件", "档位", "预测P", "区间低", "区间高"]
# 与既有工作区的 分析\校准历史.tsv 保持同样的 7 列——那个文件是 append-only 的，
# 擅自加列会让旧行与新行对不齐。ECE 只出现在报告正文里。
CALIB_COLS = ["日期", "事件数", "判定数", "预测数", "Brier", "LogLoss", "基线Brier"]


class Config(object):
    """模型变体开关（消融实验就是换这几个开关跑回测）。"""

    def __init__(self, name="pooled_retry", tier_pooling=True, retry_term=True,
                 fit_gamma=False, hint_offsets=True, atom_difficulty=True):
        self.name = name
        self.tier_pooling = tier_pooling
        self.retry_term = retry_term
        self.fit_gamma = fit_gamma
        self.hint_offsets = hint_offsets
        self.atom_difficulty = atom_difficulty

    def as_dict(self):
        return dict(name=self.name, tier_pooling=self.tier_pooling,
                    retry_term=self.retry_term, fit_gamma=self.fit_gamma,
                    hint_offsets=self.hint_offsets, atom_difficulty=self.atom_difficulty)


VARIANTS = {
    "legacy": Config("legacy", tier_pooling=False, retry_term=False,
                     fit_gamma=False, hint_offsets=True),
    "pooled": Config("pooled", tier_pooling=True, retry_term=False,
                     fit_gamma=False, hint_offsets=True),
    "pooled_retry": Config("pooled_retry", tier_pooling=True, retry_term=True,
                           fit_gamma=False, hint_offsets=True),
    "full": Config("full", tier_pooling=True, retry_term=True,
                   fit_gamma="auto", hint_offsets=True),
}
DEFAULT_VARIANT = "pooled_retry"


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _tier_prior(tier):
    return TIER_PRIOR.get(tier or "?", TIER_PRIOR["?"])


def _resolve_gamma(rows, config):
    if config.fit_gamma != "auto":
        return bool(config.fit_gamma)
    seen = set(r["dt"] for r in rows if r["dt"] > 0)
    return len(seen) >= 8 and sum(1 for r in rows if r["dt"] > 0) >= 40


class Model(object):
    def __init__(self, config, theta=THETA0, subj=None, tier=None, delta=None,
                 gamma=GAMMA0, retry=0.0, fit_gamma=False, iters=0, converged=True,
                 grad_inf=0.0, nll=0.0, trace=None, n=0, se_theta=0.6,
                 stop_reason="cold_start"):
        self.config = config
        self.theta = theta
        self.subj = subj or {}
        self.tier = tier or dict(TIER_PRIOR)
        self.delta = delta or {}
        self.gamma = gamma
        self.retry = retry
        self.fit_gamma = fit_gamma
        self.iters = iters
        self.converged = converged
        self.grad_inf = grad_inf
        self.nll = nll
        self.trace = trace or []
        self.n = n
        self.se_theta = se_theta
        self.stop_reason = stop_reason
        self.prior_delta = {}

    def difficulty(self, atom, tier, hint=""):
        base = self.tier.get(tier or "?", _tier_prior(tier))
        if not self.config.tier_pooling:
            base = 0.0
        d = base + self.delta.get(atom, self.prior_delta.get(atom, HINT_SHIFT.get(hint, 0.0)))
        return d

    def predict(self, atom="", subject="", tier="?", dt=0.0, retry=0.0, hint=""):
        d = self.difficulty(atom, tier, hint)
        r = self.retry if self.config.retry_term else 0.0
        z = self.theta + self.subj.get(subject, 0.0) - d - self.gamma * dt + r * retry
        p = sigmoid(z)
        n_obs = self.n_obs.get(atom, 0) if hasattr(self, "n_obs") else 0
        se = math.sqrt(self.se_theta ** 2 + (SIG_DELTA / math.sqrt(1.0 + n_obs)) ** 2)
        return p, sigmoid(z - Z90 * se), sigmoid(z + Z90 * se)

    def as_dict(self):
        return dict(config=self.config.as_dict(), theta=round(self.theta, 4),
                    tier=dict((k, round(v, 4)) for k, v in self.tier.items()),
                    gamma=round(self.gamma, 5),
                    retry=round(self.retry, 4), fit_gamma=self.fit_gamma,
                    iters=self.iters, converged=self.converged,
                    grad_inf=self.grad_inf, nll=round(self.nll, 6), n=self.n,
                    stop_reason=self.stop_reason,
                    subjects=dict((k, round(v, 4)) for k, v in self.subj.items()),
                    hard_atoms=sorted(self.delta.items(), key=lambda kv: -kv[1])[:10])


def _prepare(rows, config):
    """把行整理成优化器要的数组：一次遍历，拟合里不再扫原始行。"""
    atoms = sorted(set(r["atom"] for r in rows))
    subjects = sorted(set(r.get("subject") or "?" for r in rows))
    aidx = dict((a, i) for i, a in enumerate(atoms))
    sidx = dict((s, i) for i, s in enumerate(subjects))
    last_hint = {}
    for r in rows:
        last_hint[r["atom"]] = r.get("prior", "")
    mu_delta = [0.0] * len(atoms)
    if config.hint_offsets:
        for a, h in last_hint.items():
            if a in aidx:
                mu_delta[aidx[a]] = HINT_SHIFT.get(h, 0.0)
    data = []
    for r in rows:
        t = r.get("tier") if r.get("tier") in TIERS else "?"
        data.append((aidx[r["atom"]], sidx.get(r.get("subject") or "?", 0),
                     TIERS.index(t), float(r.get("dt") or 0.0),
                     float(r.get("retry") or 0.0), 1.0 if r["y"] else 0.0))
    return dict(atoms=atoms, subjects=subjects, aidx=aidx, sidx=sidx,
                mu_delta=mu_delta, data=data)


def objective_and_grad(params, prep, config, fit_gamma):
    n_atom = len(prep["atoms"])
    n_subj = len(prep["subjects"])
    theta = params[0]
    subj = params[1:1 + n_subj]
    tier = params[1 + n_subj:1 + n_subj + 4]
    delta = params[1 + n_subj + 4:1 + n_subj + 4 + n_atom]
    gamma = params[-2] if fit_gamma else GAMMA0
    if fit_gamma:
        retry = params[-1]
    else:
        retry = params[-1]
    f = 0.0
    g = [0.0] * len(params)
    for ai, si, ti, dt, rt, y in prep["data"]:
        d = (tier[ti] if config.tier_pooling else 0.0) + delta[ai]
        z = theta + subj[si] - d - gamma * dt + (retry * rt if config.retry_term else 0.0)
        p = sigmoid(z)
        if y > 0.5:
            f -= math.log(max(p, 1e-12))
        else:
            f -= math.log(max(1.0 - p, 1e-12))
        e = p - y
        g[0] += e
        g[1 + si] += e
        if config.tier_pooling:
            g[1 + n_subj + ti] -= e
        g[1 + n_subj + 4 + ai] -= e
        if config.retry_term:
            g[-1] += e * rt
        if fit_gamma:
            g[-2] += -e * dt
    # 先验项（L2 = 高斯先验的负对数似然，差常数）
    f += 0.5 * ((theta - THETA0) / SIG_THETA) ** 2
    g[0] += (theta - THETA0) / (SIG_THETA ** 2)
    for i in range(n_subj):
        f += 0.5 * (subj[i] / SIG_SUBJ) ** 2
        g[1 + i] += subj[i] / (SIG_SUBJ ** 2)
    for i in range(4):
        mu = TIER_PRIOR[TIERS[i]]
        f += 0.5 * ((tier[i] - mu) / SIG_TIER) ** 2
        g[1 + n_subj + i] += (tier[i] - mu) / (SIG_TIER ** 2)
    for i in range(n_atom):
        mu = prep["mu_delta"][i]
        f += 0.5 * ((delta[i] - mu) / SIG_DELTA) ** 2
        g[1 + n_subj + 4 + i] += (delta[i] - mu) / (SIG_DELTA ** 2)
    if fit_gamma:
        f += 0.5 * ((gamma - GAMMA0) / SIG_GAMMA) ** 2
        g[-2] += (gamma - GAMMA0) / (SIG_GAMMA ** 2)
    f += 0.5 * (retry / SIG_RETRY) ** 2
    g[-1] += retry / (SIG_RETRY ** 2)
    return f, g


def fit(rows, config=None, max_iter=400, tol=1e-5, gtol=1e-4):
    """MAP 拟合（线搜索）。

    返回 Model；空数据返回先验模型（不报错，冷启动要能用）。
    """
    config = config or VARIANTS[DEFAULT_VARIANT]
    if isinstance(config, str):
        config = VARIANTS.get(config, VARIANTS[DEFAULT_VARIANT])
    prep = _prepare(rows, config)
    n_atom = len(prep["atoms"])
    n_subj = len(prep["subjects"])
    fit_gamma = _resolve_gamma(rows, config)
    params = [THETA0] + [0.0] * n_subj + [TIER_PRIOR[t] for t in TIERS] \
        + [prep["mu_delta"][i] for i in range(n_atom)] + [GAMMA0, 0.0]
    if not prep["data"]:
        m = _model_from(params, prep, config, fit_gamma, 0, True, 0.0, 0.0, [])
        return m
    f, g = objective_and_grad(params, prep, config, fit_gamma)
    trace = [f]
    converged = False
    stop_reason = "max_iter"
    alpha = 2.0 / (1.0 + len(prep["data"]))
    for it in range(max_iter):
        gnorm = max(abs(x) for x in g) if g else 0.0
        if gnorm < gtol:
            converged = True
            stop_reason = "gradient"
            break
        step = alpha
        accepted = False
        for _ in range(40):
            cand = [params[i] - step * g[i] for i in range(len(params))]
            f2, g2 = objective_and_grad(cand, prep, config, fit_gamma)
            if f2 <= f - 1e-4 * step * sum(x * x for x in g):
                params, f, g = cand, f2, g2
                accepted = True
                break
            step *= 0.5
        if not accepted:
            converged = True
            stop_reason = "step_too_small"
            break
        alpha = min(step * 1.5, alpha * 4.0 + 1e-6)
        trace.append(f)
        if abs(trace[-2] - f) <= tol * (1.0 + abs(f)):
            converged = True
            stop_reason = "objective_stable"
            break
    m = _model_from(params, prep, config, fit_gamma, len(trace), converged,
                    max(abs(x) for x in g) if g else 0.0, f, trace)
    m.stop_reason = stop_reason
    m.se_theta = fisher_se(m, prep)
    return m


def _model_from(params, prep, config, fit_gamma, iters, converged, grad_inf, nll, trace):
    n_subj = len(prep["subjects"])
    n_atom = len(prep["atoms"])
    subj = dict((prep["subjects"][i], params[1 + i]) for i in range(n_subj))
    tier = dict((TIERS[i], params[1 + n_subj + i]) for i in range(4))
    delta = dict((prep["atoms"][i], params[1 + n_subj + 4 + i]) for i in range(n_atom))
    gamma = params[-2] if fit_gamma else GAMMA0
    m = Model(config, theta=params[0], subj=subj, tier=tier, delta=delta,
              gamma=gamma, retry=params[-1], fit_gamma=fit_gamma, iters=iters,
              converged=converged, grad_inf=grad_inf, nll=nll, trace=trace,
              n=len(prep["data"]))
    m.prior_delta = dict((prep["atoms"][i], prep["mu_delta"][i]) for i in range(n_atom))
    obs = {}
    for ai, _si, _ti, _dt, _rt, _y in prep["data"]:
        obs[prep["atoms"][ai]] = obs.get(prep["atoms"][ai], 0) + 1
    m.n_obs = obs
    return m


def fisher_se(model, prep):
    """theta 的标准误（Fisher 信息对角线近似）：给预测区间一个诚实的宽度。"""
    h = 1.0 / (SIG_THETA ** 2)
    n_subj = len(prep["subjects"])
    for ai, si, ti, dt, rt, _y in prep["data"]:
        d = model.tier.get(TIERS[ti], _tier_prior(TIERS[ti])) + model.delta.get(prep["atoms"][ai], 0.0)
        z = model.theta + model.subj.get(prep["subjects"][si], 0.0) - d \
            - model.gamma * dt + (model.retry * rt if model.config.retry_term else 0.0)
        p = sigmoid(z)
        h += p * (1.0 - p)
    return 1.0 / math.sqrt(h)


def predict_next(rows, target, config=None):
    """先预测后作答：用「截至此刻」的数据预测目标行（target 不在训练集里）。"""
    model = fit(rows, config)
    return model.predict(atom=target.get("atom", ""), subject=target.get("subject") or "",
                         tier=target.get("tier") or "?", dt=float(target.get("dt") or 0.0),
                         retry=float(target.get("retry") or 0.0),
                         hint=target.get("prior", "")), model


def _prior_only_predict(row):
    """冷启动（还没有任何判定样本）时的先验预测，保证可复现。"""
    d = _tier_prior(row.get("tier")) + HINT_SHIFT.get(row.get("prior", ""), 0.0)
    return sigmoid(THETA0 - d)


def walk_forward(rows, config=None, min_train=3, refit_every=None):
    """滚动回测：预测第 k 条时只用第 1..k-1 条（绝不看未来）。"""
    config = config or VARIANTS[DEFAULT_VARIANT]
    if isinstance(config, str):
        config = VARIANTS.get(config, VARIANTS[DEFAULT_VARIANT])
    n = len(rows)
    if not n:
        return dict(n=0, brier=None, logloss=None, base_brier=None, ece=None,
                    rows=[], costs=0, config=config.as_dict())
    if refit_every is None:
        refit_every = 1 if n <= 200 else max(5, n // 100)
    model = None
    costs = 0
    preds = []
    for k, r in enumerate(rows):
        if k >= min_train and (model is None or (k % refit_every == 0)):
            model = fit(rows[:k], config)
            costs += 1
        if model is None:
            p = _prior_only_predict(r)
        else:
            p = model.predict(atom=r["atom"], subject=r.get("subject") or "",
                              tier=r.get("tier") or "?", dt=float(r.get("dt") or 0.0),
                              retry=float(r.get("retry") or 0.0),
                              hint=r.get("prior", ""))[0]
        preds.append((r, p))
    ys = [r["y"] for r, _ in preds]
    ps = [p for _, p in preds]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    ll = -sum(y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9))
              for p, y in zip(ps, ys)) / n
    base, ok, tot = [], 0, 0
    for y in ys:
        base.append((ok + 0.5) / (tot + 1.0))
        ok += y
        tot += 1
    base_brier = sum((p - y) ** 2 for p, y in zip(base, ys)) / n
    return dict(n=n, brier=brier, logloss=ll, base_brier=base_brier,
                ece=ece(ps, ys), rows=preds, costs=costs,
                config=config.as_dict())


def ece(ps, ys, bins=10):
    """期望校准误差：预测 70% 的那些题，实际通过率离 70% 有多远。"""
    if not ps:
        return None
    tot = len(ps)
    err = 0.0
    for b in range(bins):
        lo, hi = b / float(bins), (b + 1) / float(bins)
        sel = [(p, y) for p, y in zip(ps, ys) if (lo <= p < hi or (b == bins - 1 and p >= hi))]
        if not sel:
            continue
        mp = sum(p for p, _ in sel) / len(sel)
        my = sum(y for _, y in sel) / len(sel)
        err += (len(sel) / float(tot)) * abs(mp - my)
    return err


def reliability(ps, ys, bins=5):
    out = []
    for b in range(bins):
        lo, hi = b / float(bins), (b + 1) / float(bins)
        sel = [(p, y) for p, y in zip(ps, ys) if (lo <= p < hi or (b == bins - 1 and p >= hi))]
        out.append(dict(lo=lo, hi=hi, n=len(sel),
                        predicted=(sum(p for p, _ in sel) / len(sel)) if sel else None,
                        actual=(sum(y for _, y in sel) / len(sel)) if sel else None))
    return out


def ablation(rows, names=None, min_train=3, refit_every=None):
    """模型变体在真实事件流上的 walk-forward 对比：用证据选模型，而不是拍脑袋。"""
    names = names or ["legacy", "pooled", "pooled_retry", "full"]
    out = []
    for nm in names:
        cfg = VARIANTS.get(nm)
        if not cfg:
            continue
        res = walk_forward(rows, cfg, min_train=min_train, refit_every=refit_every)
        out.append(dict(variant=nm, n=res["n"], brier=res["brier"], logloss=res["logloss"],
                        base_brier=res["base_brier"], ece=res["ece"], refits=res["costs"]))
    out.sort(key=lambda x: (x["brier"] if x["brier"] is not None else 9e9))
    return out


def pattern_risk(rows, half_life=14.0, today=None):
    """模式风险：P(再次失败 | 混淆模式)，Beta 后验 + 时近加权。"""
    if today is None:
        today = datetime.date.today().isoformat()
    acc = {}
    for r in rows:
        if not r.get("mode"):
            continue
        try:
            age = max(0.0, (datetime.date.fromisoformat(today)
                            - datetime.date.fromisoformat(r["date"])).days)
        except Exception:
            age = 0.0
        w = 0.5 ** (age / half_life)
        for m in re.split(r"[、/]", r["mode"]):
            m = m.strip()
            if not m:
                continue
            a = acc.setdefault(m, [0.0, 0.0, 0])
            a[0 if r["y"] == 0 else 1] += w
            a[2] += 1
    out = []
    for m, (fail, ok, n) in sorted(acc.items()):
        a_, b_ = 1.0 + fail, 1.0 + ok
        mean = a_ / (a_ + b_)
        sd = math.sqrt(mean * (1 - mean) / (a_ + b_ + 1))
        lo, hi = max(0.0, mean - Z90 * sd), min(1.0, mean + Z90 * sd)
        out.append(dict(mode=m, n=n, fail=round(fail, 2), ok=round(ok, 2),
                        p=mean, lo=lo, hi=hi, trigger=(lo >= 0.30 and n >= 2)))
    out.sort(key=lambda x: -x["p"])
    return out


def teach_rank(rows, window_days=10):
    """讲法排名：谁的「直讲之后第一次判定」通过率高（Wilson 下界排序）。

    归因在 to_dataset 里完成——直讲事件的讲法码会挂到该原子后面那次判定上，
    所以这里只数「带讲法码的判定行」：一次判定就是该讲法的一次战绩。
    超过 window_days 才考的，说明早忘了，不算这次直讲的功劳。
    """
    acc = {}
    for r in rows:
        t = (r.get("teach") or "").strip()
        if not t:
            continue
        if (r.get("teach_dt") or 0.0) > window_days:
            continue
        for code in re.split(r"[、/,]", t):
            code = code.strip()
            if not code:
                continue
            a = acc.setdefault(code, [0, 0])
            a[0 if r["y"] == 1 else 1] += 1
    out = []
    for t, (ok, bad) in sorted(acc.items()):
        tot = ok + bad
        p = ok / float(tot)
        z = Z90
        den = 1 + z * z / tot
        c = p + z * z / (2 * tot)
        hw = z * math.sqrt(p * (1 - p) / tot + z * z / (4 * tot * tot))
        out.append(dict(teach=t, n=tot, ok=ok, bad=bad, p=p,
                        lo=max(0.0, (c - hw) / den), hi=min(1.0, (c + hw) / den),
                        explore=tot < 3))
    out.sort(key=lambda x: -(x["p"] if x["p"] is not None else -1))
    return out


def learning_curve(res, buckets=4):
    """越用越准的证据：把回测按时间切片，看 Brier 是否逐段下降。"""
    rows = res.get("rows") or []
    n = len(rows)
    if n < buckets * 2:
        return []
    size = max(1, n // buckets)
    out = []
    for b in range(buckets):
        lo = b * size
        hi = n if b == buckets - 1 else (b + 1) * size
        seg = rows[lo:hi]
        if not seg:
            continue
        err = sum((p - r["y"]) ** 2 for r, p in seg) / len(seg)
        out.append(dict(seg=b + 1, lo=lo, hi=hi, n=len(seg), brier=err))
    return out


def append_prediction(path, atom, event, stage, p, lo, hi, today=None, when=None):
    row = [today or datetime.date.today().isoformat(),
           when or datetime.datetime.now().strftime("%H:%M"),
           atom, event or "首测", stage or "—",
           "%.3f" % p, "%.3f" % lo, "%.3f" % hi]
    fresh = not read_lines(path)
    header = "\t".join(PRED_COLS) + "\n" if fresh else ""
    append_text(path, header + "\t".join(str(x) for x in row) + "\n", bom_if_new=False)
    return row


def calibration_summary(ws_stream_rows, config=None):
    res = walk_forward(ws_stream_rows, config or VARIANTS[DEFAULT_VARIANT])
    return res

# -*- coding: utf-8 -*-
"""算法层测试：真参数能不能被估回来、收敛是否可判定、预测是否只用过去。"""
import math
import random
import time
import unittest

from tests.util import PROJECT
from kaoyan_mcp import learn


def synth(n=400, seed=7, theta=1.0, gamma=math.log(2) / 10.0, spread=0.7):
    """造一批「有真值」的判定事件：S/A/B 三档难度不同，原子各有偏离。"""
    rng = random.Random(seed)
    tiers = ["S", "A", "B"]
    tier_mu = {"S": 0.30, "A": 0.15, "B": 0.05}
    atoms = []
    for i in range(40):
        t = tiers[i % 3]
        atoms.append(dict(atom="AT-%02d-%03d" % (i % 3, i), tier=t,
                          subject="合成" + t, delta=rng.gauss(0, spread)))
    rows = []
    day = 0
    for k in range(n):
        a = atoms[rng.randrange(len(atoms))]
        dt = rng.choice([0.0, 1.0, 2.0, 4.0, 7.0, 14.0])
        d = tier_mu[a["tier"]] + a["delta"]
        z = theta - d - gamma * dt
        p = 1.0 / (1.0 + math.exp(-z))
        y = 1 if rng.random() < p else 0
        rows.append(dict(date="2026-%02d-%02d" % (1 + k // 28, 1 + k % 28), atom=a["atom"],
                         subject=a["subject"], chapter="合成", tier=a["tier"], mode="",
                         teach="", verdict="✅" if y else "⚠️", y=y, dt=dt, retry=0.0,
                         prior=""))
    return rows, atoms, theta, gamma


class FitConvergenceTest(unittest.TestCase):
    def test_converges_and_recovers_theta(self):
        rows, atoms, theta, gamma = synth()
        cfg = learn.Config("test", fit_gamma=True)
        t0 = time.time()
        m = learn.fit(rows, cfg)
        elapsed = time.time() - t0
        self.assertTrue(m.converged, "线搜索应当收敛")
        self.assertLess(m.iters, 400)
        self.assertLess(elapsed, 8.0)
        self.assertAlmostEqual(m.theta, theta, delta=0.35)
        self.assertIn(m.stop_reason, ("gradient", "objective_stable", "step_too_small"))

    def test_recovers_forgetting_rate_at_scale(self):
        rows, atoms, theta, gamma = synth(n=800, seed=11)
        m = learn.fit(rows, learn.Config("test", fit_gamma=True))
        self.assertAlmostEqual(m.gamma, gamma, delta=0.03)
        self.assertTrue(m.fit_gamma)

    def test_gamma_stays_fixed_when_data_is_thin(self):
        rows, atoms, theta, gamma = synth(n=30, seed=3)
        m = learn.fit(rows, learn.VARIANTS["full"])
        self.assertFalse(m.fit_gamma, "样本太少时不自由拟合遗忘常数")
        self.assertAlmostEqual(m.gamma, learn.GAMMA0)

    def test_tier_ordering_is_recovered(self):
        rows, atoms, theta, gamma = synth(n=600, seed=5)
        m = learn.fit(rows)
        self.assertGreater(m.tier["S"], m.tier["B"])
        self.assertGreater(m.tier["S"], m.tier["A"])

    def test_fit_is_linear_ish_in_events(self):
        rows, _, _, _ = synth(n=2000, seed=13)
        t0 = time.time()
        learn.fit(rows)
        self.assertLess(time.time() - t0, 20.0, "事件流变长不该出现平方级退化")


class PredictionDisciplineTest(unittest.TestCase):
    def test_walk_forward_never_sees_the_future(self):
        rows, _, _, _ = synth(n=60, seed=17)
        full = learn.walk_forward(rows, learn.VARIANTS["pooled_retry"], min_train=3,
                                  refit_every=1)
        prefix = learn.walk_forward(rows[:40], learn.VARIANTS["pooled_retry"], min_train=3,
                                    refit_every=1)
        p_full = full["rows"][39][1]
        p_prefix = prefix["rows"][-1][1]
        self.assertAlmostEqual(p_full, p_prefix, places=9,
                               msg="第 40 条之前的预测必须只依赖前 39 条")

    def test_ablation_covers_all_variants(self):
        rows, _, _, _ = synth(n=120, seed=19)
        out = learn.ablation(rows, refit_every=5)
        names = [r["variant"] for r in out]
        for want in ("legacy", "pooled", "pooled_retry", "full"):
            self.assertIn(want, names)
        briers = [r["brier"] for r in out]
        self.assertEqual(briers, sorted(briers), "应当按照 Brier 从好到差排序")

    def test_ece_bounds(self):
        ps = [0.9] * 10
        ys = [1] * 5 + [0] * 5      # 预测 90% 实际 50% -> ECE 应当约 0.4
        e = learn.ece(ps, ys, bins=10)
        self.assertAlmostEqual(e, 0.4, places=6)
        self.assertEqual(learn.ece([0.5] * 4, [1, 0, 1, 0], bins=2), 0.0)

    def test_cold_start_uses_priors_only(self):
        rows, _, _, _ = synth(n=5, seed=23)
        res = learn.walk_forward(rows, min_train=3)
        self.assertEqual(res["n"], 5)
        self.assertTrue(all(0.0 < p < 1.0 for _, p in res["rows"]))


if __name__ == "__main__":
    unittest.main()

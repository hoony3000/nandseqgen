# dist.py (분포 샘플러)
import random
def sample_dist(d):
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal":
        m = d["mean"]; s = d["std"]; mn = d.get("min", 0.0)
        v = random.gauss(m, s); return max(v, mn)
    if k == "exp":
        l = d["lambda"]; return random.expovariate(l)
    if k == "categorical":
        # {"kind":"categorical","items":[["A",0.3],["B",0.7]]}
        import bisect
        items = d["items"]
        ps=[]; xs=[]
        acc=0.0
        for x,p in items:
            acc+=p; ps.append(acc); xs.append(x)
        r=random.random()*acc
        i= next(i for i,pp in enumerate(ps) if r<=pp)
        return xs[i]
    raise ValueError(f"unknown dist kind: {k}")
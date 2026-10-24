"""Independently authored One Euro filter and inference timing governor."""
import math
import numpy as np


class OneEuro:
    def __init__(self, cutoff=2., beta=.025):
        self.cutoff, self.beta = cutoff,beta
        self.t = self.raw = self.value = None
        self.velocity = 0.

    @staticmethod
    def alpha(cutoff,dt):
        return 1./(1.+1./(2*math.pi*cutoff*dt))

    def update(self,value,t):
        value = np.asarray(value,dtype=float)
        if self.t is None or t <= self.t or t-self.t > .5:
            self.value = self.raw = value.copy()
            self.velocity = np.zeros_like(value)
        else:
            dt = t-self.t
            a = self.alpha(1.,dt)
            self.velocity = a*(value-self.raw)/dt+(1-a)*self.velocity
            a = self.alpha(self.cutoff+self.beta*np.abs(self.velocity),dt)
            self.value = a*value+(1-a)*self.value
            self.raw = value.copy()
        self.t = t
        return self.value.copy()


class Cadence:
    def __init__(self, interval_ms, search_ms):
        self.base = interval_ms/1000
        self.search = search_ms/1000
        self.cost = .01
        self.last = -math.inf

    def due(self,now,lost):
        if self.base==0:
            return True
        # Share the CPU with flow, GUI and audio; search still runs at the next budgeted slot.
        # Keep a small CPU cooldown while lost without the old 1.4x penalty.
        # The engine separately bounds inference to one pending call.
        interval = max(self.search,self.cost*.5) if lost else max(self.base,min(.25,self.cost*1.4))
        return now-self.last >= interval

    def done(self,start,end):
        self.last = end
        self.cost = .8*self.cost+.2*(end-start)

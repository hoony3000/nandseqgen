NS = 1
US = 10**3
MS = 10**6
S = 10**9

import bisect
from typing import Union, List, Tuple, TypeVar, Generic, Callable, Any, Set
import itertools
import hashlib
import threading
import weakref
from dataclasses import dataclass, field
import numpy as np
import yaml
import os

# 중복 instance 생성 방지를 위한 parent class 정의
class DeduplicateBase
  """
  multi-thread 환경(필요하다면)에서 instance 중복 생성 방지를 위한 id, lock, counter
  """
  _global_lock = threading.Lock()
  _global_instances = weakref.WeakValueDictionary()
  _class_counters = {}
  _class_instances_by_id = {}

  @classmethod
  def create(cls, *args, force_new=False, **kwargs):
    """
    instance 생성자
    """
    key = cls._make_key(*args, **kwargs)
    if not force_new:
      with cls._global_lock:
        instance = cls._global_instances.get((cls, key))
        if instance:
          return instance

    obj = cls(*args, **kwargs)
    obj.id = next(cls._get_class_counter())

    with cls._global_lock:
      cls._class_instances_by_id[cls][obj.id] = obj
      if not force_new:
        cls._global_instances[(cls, key)] = obj

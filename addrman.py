import numpy as np
import itertools
import seaborn as sns
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.lines import Line2D
from matplotlib import cm
from matplotlib.colors import Normalize
from mpl_toolkits.mplot3d.art3d import Line3DCollection

# from loguru import logger
TLC = "TLC"
FWSLC = "FWSLC"
SLC = "SLC"
TBD = "TBD"
BAD = -3
GOOD = -2
ERASE = -1

CMD_VOCAB = {"ERASE": 0, "PGM": 1, "READ": 2}
ADDR_KEYS = ["plane", "block", "page"]


def arr_to_nparr(adds: list | np.ndarray):
    if isinstance(adds, (list, tuple, set)):
        if all(isinstance(v, int) for v in adds):
            return np.array(adds, dtype=int)
        else:
            raise TypeError("All elements in adds must be integers.")
    elif isinstance(adds, int):
        return np.array([adds], dtype=int)
    elif isinstance(adds, np.ndarray) and adds.dtype.kind in {"i", "u"}:
        return adds
    else:
        raise TypeError(
            f"adds must be a list, tuple, set, int, or numpy array of integers: {type(adds)}"
        )


def reduce_to_blkarr(adds: np.ndarray):
    if isinstance(adds, np.ndarray) and adds.dtype.kind in {"i", "u"}:
        return adds.reshape(-1, adds.shape[-1])[:, 0]
    else:
        raise TypeError(f"adds must be a numpy array of integers: {type(adds)}")


def to_1D_blkaddr(adds: list | np.ndarray):
    tmp_adds = arr_to_nparr(adds)
    if tmp_adds.ndim > 1:
        tmp_adds = reduce_to_blkarr(tmp_adds)

    return tmp_adds


def empty_arr():
    return np.array([], dtype=int)


def all_subsets(s):
    return list(
        itertools.chain.from_iterable(
            itertools.combinations(s, r) for r in range(1, len(s) + 1)
        )
    )


class AddressManager:
    """
    AddressManager class 정의
    num_address : block address 갯수
    addrstates : address 상태를 저장하는 numpy 배열
    addrmodes : TLC or SLC mode 를 저장하는 numpy 배열
    pagesize : block 내 page 갯수
    offset : addrReadable 구할 때 last PGM page address 끝에서부터 제외할 page 갯수
    addrErasable : 현재 erase 가능한 block address list
    addrPGMable : 현재 PGM 가능한 [block, page] address list
    addrReadable : 현재 read 가능한 [block, page] address list
    undo_addrs : 마지막 erase 또는 PGM 했던 address list
    undo_states : 마지막 erase 또는 PGM 했던 address 의 addrstates
    undo_modes : 마지막 erase 또는 PGM 했던 address 의 addrmodes
    oversample : 전체 가능한 address 갯수보다 더 많은 sample 을 요구했을 떄 True
    """

    # adds 배열의 상태 값 정의
    # -3: badblock
    # -2: goodblock not erased
    # -1: erased
    # 0 to pagesize-1 : PGM 된 page 수
    set_plane: set = {0, 1, 2, 4, 6}

    def __init__(
        self,
        num_planes: int,
        num_blocks: int,
        pagesize: int,
        init: int = GOOD,
        badlist=np.array([], dtype=int),
        offset: int = 30,
    ):
        """
        생성자 정의
        """
        self.num_planes: int
        self.num_blocks: int
        self.addrstates: np.ndarray
        self.addrmodes: np.ndarray
        self.pagesize: int
        self.offset: int
        self.addrErasable: np.ndarray = np.array([], dtype=int)
        self.addrPGMable: np.ndarray = np.array([], dtype=int)
        self.addrReadable: np.ndarray = np.array([], dtype=int)
        self.undo_addrs: np.ndarray = np.array([], dtype=int)
        self.undo_states: np.ndarray = np.array([], dtype=int)
        self.undo_modes: np.ndarray = np.array([], dtype=int)
        self.oversample: bool = False

        if num_planes in AddressManager.set_plane:
            self.num_planes = num_planes
        else:
            raise ValueError(
                f"num_planes must be one of {AddressManager.set_plane}, got {num_planes}"
            )

        if isinstance(num_blocks, int) and num_blocks > 0:
            self.num_blocks = num_blocks
            if isinstance(init, int) and init > BAD or init < pagesize:
                self.addrstates = np.full(num_blocks, init, dtype=int)
                self.addrmodes = np.full(
                    num_blocks, TBD, dtype=object
                )  # dtype=str 시 오동작
                adds = arr_to_nparr(badlist)
                self.addrstates[adds] = BAD
            else:
                raise ValueError(
                    f"init must be an integer greater than {BAD} or less than {pagesize}, got {init}"
                )
        else:
            raise ValueError(f"num_blocks must be a positive integer, got {num_blocks}")

        if isinstance(pagesize, int) and pagesize > 0:
            self.pagesize = pagesize
        else:
            raise ValueError(f"pagesize must be a positive integer, got {pagesize}")

        if isinstance(offset, int) and offset >= 0 and offset < pagesize:
            self.offset = offset
        else:
            raise ValueError(
                f"offset must be a non-negative integer less than pagesize, got {offset}"
            )

    def set_range_val(self, add_from: int, add_to: int, val: int, mode=TLC):
        """
        adds 배열에서 add_from 부터 add_to 까지의 index 에 val 값을 할당
        """
        self.addrstates[add_from : add_to + 1] = val
        self.addrmodes[add_from : add_to + 1] = mode

    def set_n_val(self, add_from: int, n: int, val: int, mode=TLC):
        """
        adds 배열에서 add_from 부터 n 개의 index 에 val 값을 할당
        """
        if add_from + n > self.num_blocks:
            raise IndexError(
                f"add_from + n exceeds num_blocks: {add_from} + {n} > {self.num_blocks}"
            )
        self.addrstates[add_from : add_from + n] = val
        self.addrmodes[add_from : add_from + n] = mode

    def set_adds_val(self, adds: np.ndarray, val: int, mode=TLC):
        """
        adds 배열에 val 값을 할당
        """
        tmp_adds = to_1D_blkaddr(adds)  # 1차원 배열

        if np.any(tmp_adds >= self.num_blocks):
            raise IndexError(
                f"Some addresses in adds exceed num_blocks: {tmp_adds[tmp_adds >= self.num_blocks]}"
            )
        self.addrstates[tmp_adds] = val
        self.addrmodes[tmp_adds] = mode

    def undo_last(self):
        """
        마지막에 했던 set_adds_erase, 또는 set_adds_pgm 의 동작을 되돌림
        """

        self.addrstates[self.undo_addrs] = self.undo_states
        self.addrmodes[self.undo_addrs] = self.undo_modes

    def set_adds_erase(self, adds: np.ndarray, mode=TLC):
        """
        adds 배열에 erase 동작을 수행
        len(adds.shape) = 1
        """
        tmp_adds = to_1D_blkaddr(adds)  # 1차원 배열
        adds_all = self.get_erasable()

        if len(tmp_adds) == 0 or len(adds_all) == 0:
            return empty_arr()

        adds_all = reduce_to_blkarr(adds_all)  # 1차원 배열
        if all(add in adds_all for add in tmp_adds):
            if all(val != BAD for val in self.addrstates[tmp_adds]):
                # in case of abortion to restore addrstates, addrmodes
                self.undo_addrs = tmp_adds
                self.undo_states = self.addrstates[tmp_adds].copy()
                self.undo_modes = self.addrmodes[tmp_adds].copy()

                self.addrstates[tmp_adds] = ERASE
                self.addrmodes[tmp_adds] = mode

                return adds
            else:
                raise ValueError(f"every value in adds must not be -3(BAD)")
        else:
            raise ValueError(f"every value in adds must be in addrErasable")

    def set_adds_pgm(self, adds: np.ndarray, mode=TLC):
        """
        adds 배열에 pgm 동작을 수행
        len(adds.shape) = 1
        """
        tmp_adds = to_1D_blkaddr(adds)  # 1차원 배열
        adds_all = self.get_pgmable(mode=mode)

        if len(tmp_adds) == 0 or len(adds_all) == 0:
            return empty_arr()

        adds_all = reduce_to_blkarr(adds_all)  # 1차원 배열
        if all(add in adds_all for add in tmp_adds):
            if all(val == mode for val in self.addrmodes[tmp_adds]):
                # in case of abortion to restore addrstates, addrmodes
                self.undo_addrs = tmp_adds
                self.undo_states = self.addrstates[tmp_adds].copy()
                self.undo_modes = self.addrmodes[tmp_adds].copy()

                self.addrstates[tmp_adds] += 1

                return adds
            else:
                raise ValueError(f"every value in addrmodes must be equal to mode arg")
        else:
            raise ValueError(f"every value in adds must be in addrPGMable")

    def get_addrstates(self) -> np.ndarray:
        """
        addrstates 반환
        """
        return self.addrstates

    def get_addrmodes(self) -> np.ndarray:
        """
        addrmodes 반환
        """
        return self.addrmodes

    def get_vals_adds(self, adds: np.ndarray) -> np.ndarray:
        """
        adds 배열의 값을 반환
        """
        tmp_adds = to_1D_blkaddr(adds)
        return self.addrstates[tmp_adds]

    def tolist(self, adds: np.ndarray = None):
        """
        adds 배열을 list 형태로 반환
        output : (addrstates[0], addrmodes[0]), (addrstates[1], addrmodes[1]), ...
        """
        if adds is None:
            return list(zip(self.addrstates.tolist(), self.addrmodes.tolist()))
        else:
            return list(
                zip(self.addrstates[adds].tolist(), self.addrmodes[adds].tolist())
            )

    def log(self, adds: np.ndarray = None, file=None):
        """
        adds 배열의 상태를 로그로 출력
        """
        if adds is None:
            if file is None:
                for i, add in enumerate(self.tolist()):
                    print(f"{i} : {add}")
            else:
                for i, add in enumerate(self.tolist()):
                    file.write(f"{i} : {add}\n")
        else:
            tmp_adds = to_1D_blkaddr(adds)
            if file is None:
                for i, add in enumerate(self.tolist(tmp_adds)):
                    print(f"{tmp_adds[i]} : {add}")
            else:
                for i, add in enumerate(self.tolist(tmp_adds)):
                    file.write(f"{tmp_adds[i]} : {add}\n")

    def get_size(self):
        """
        addrstates 배열의 크기를 반환
        """
        return self.num_blocks

    def _get_multi_adds(self, sel_plane: list):
        """
        adds 배열에서 BAD 가 아닌 값을 갖는 index 반환
        sel_plane : 선택된 plane 인덱스 리스트
        output : (blockaddr0, blockaddr1, ...)
        """
        sub_indices = np.arange(self.num_blocks).reshape(-1, self.num_planes)
        sub_indices = sub_indices[:, sel_plane]
        sub_vals = self.addrstates[sub_indices]

        mask = np.all(sub_vals != BAD, axis=1)

        return sub_indices[mask]

    def _get_erasable(self, sel_plane: int = None):
        """
        adds 배열에서 BAD 가 아닌 값을 갖는 index 반환
        sel_plane : 선택된 plane 인덱스
        output : (blockaddr0, blockaddr1, ...)
        """
        indice = np.arange(self.num_blocks)
        indice = indice % self.num_planes
        if sel_plane is None:
            blkadds = np.where((self.addrstates != BAD) & (self.addrstates != ERASE))[0]
        else:
            blkadds = np.where(
                (self.addrstates != BAD)
                & (self.addrstates != ERASE)
                & (indice == sel_plane)
            )[0]

        if len(blkadds) == 0:
            return empty_arr()

        return np.dstack((blkadds, np.zeros_like(blkadds))).reshape(
            blkadds.shape[0], 1, -1
        )  # shape: (?, 1, 2)

    def _get_multi_erasable(self, sel_plane: list):
        """
        adds 배열에서 BAD 가 아닌 값을 갖는 index 반환
        sel_plane : 선택된 plane 인덱스 리스트
        output : ((blockaddr0, blockaddr1), (blockaddr2, blockaddr3), ...)
        """
        sub_indices = self._get_multi_adds(sel_plane=sel_plane)
        sub_vals = self.addrstates[sub_indices]

        mask = np.all((sub_vals != ERASE), axis=1)

        blkadds = sub_indices[mask]

        if len(blkadds) == 0:
            return empty_arr()

        return np.dstack(
            (blkadds, np.zeros_like(blkadds))
        )  # shape: (?, len(sel_plane), 2)

    def _get_pgmable(self, mode=TLC, sel_plane: int = None):
        """
        adds 배열에서 pagesize-1 보다 작은 값을 갖는 index 반환
        mode : cell mode, ex) SLC|FWSLC|TLC
        sel_plane : 선택된 plane 인덱스
        output : (blockaddr0, pageaddr0), (blockaddr1, pageaddr1), ...
        """
        indice = np.arange(self.num_blocks)
        indice = indice % self.num_planes

        if sel_plane is None:
            blkadds = np.where(
                (self.addrstates >= ERASE)
                & (self.addrstates < self.pagesize - 1)
                & (self.addrmodes == mode)
            )[0]
        else:
            blkadds = np.where(
                (self.addrstates >= ERASE)
                & (self.addrstates < self.pagesize - 1)
                & (self.addrmodes == mode)
                & (indice == sel_plane)
            )[0]

        if len(blkadds) == 0:
            return empty_arr()

        return np.dstack((blkadds, self.addrstates[blkadds] + 1)).reshape(
            blkadds.shape[0], 1, -1
        )  # shape: (?, 1, 2)

    def _get_multi_pgmable(self, sel_plane: list, mode=TLC):
        """
        adds 배열에서 pagesize-1 보다 작은 값을 갖는 index 반환
        sel_plane : 선택된 plane 인덱스 리스트
        mode : cell mode, ex) SLC|FWSLC|TLC
        output : (((blockaddr0, pageaddr0), (blockaddr1, pageaddr1)), ((blockaddr2, pageaddr2), (blockaddr3, pageaddr3)), ...)
        """
        sub_indices = self._get_multi_adds(sel_plane=sel_plane)
        sub_vals = self.addrstates[sub_indices]
        sub_modes = self.addrmodes[sub_indices]

        mask = np.all(
            (sub_vals == sub_vals[:, [0]])
            & (sub_vals < self.pagesize - 1)
            & (sub_vals >= ERASE)
            & (sub_modes == mode),
            axis=1,
        )

        blkadds = sub_indices[mask]

        if len(blkadds) == 0:
            return empty_arr()

        return np.dstack(
            (blkadds, self.addrstates[blkadds] + 1)
        )  # shape: (?, len(sel_plane), 2)

    def _get_readable(self, offset: int = None, mode=TLC, sel_plane: int = None):
        """
        adds 배열에서 -1(ERASE) 보다 큰 값을 갖는 index 반환
        offset : last pgm 된 address 에서 offset 만큼 제외
        mode : cell mode, ex) SLC|FWSLC|TLC
        sel_plane : 선택된 plane 인덱스
        output : (blockaddr0, pageaddr0), (blockaddr1, pageaddr1), ...
        """
        try:
            if offset is None:
                _offset = self.offset
            else:
                _offset = offset
        except Exception as e:
            raise ValueError(f"offset 값 오류: {e}")

        indice = np.arange(self.num_blocks)
        indice = indice % self.num_planes

        if sel_plane is None:
            blkadds = np.where((self.addrstates >= _offset) & (self.addrmodes == mode))[
                0
            ]
        else:
            blkadds = np.where(
                (self.addrstates >= _offset)
                & (self.addrmodes == mode)
                & (indice == sel_plane)
            )[0]

        if len(blkadds) == 0:
            return empty_arr()

        readadds = self.addrstates[blkadds]
        readadds -= _offset

        arr_tot = []
        for idx, blkadd in enumerate(blkadds):
            arr = [(blkadd, readadd) for readadd in range(readadds[idx] + 1)]
            arr_tot.extend(arr)

        res = np.array(arr_tot, dtype=int)

        return res.reshape(res.shape[0], 1, -1)  # shape: (?, 1, 2)

    def _get_multi_readable(self, sel_plane: list, offset: int = None, mode=TLC):
        """
        adds 배열에서 -1(ERASE) 보다 큰 값을 갖는 index 반환
        sel_plane : 선택된 plane 인덱스 리스트
        offset : last pgm 된 address 에서 offset 만큼 제외
        mode : cell mode, ex) SLC|FWSLC|TLC
        output : (((blockaddr0, pageaddr0), (blockaddr1, pageaddr1)), ((blockaddr2, pageaddr2), (blockaddr3, pageaddr3)), ...)
        """
        try:
            if offset is None:
                _offset = self.offset
            else:
                _offset = offset
        except Exception as e:
            raise ValueError(f"offset 값 오류: {e}")

        sub_indices = self._get_multi_adds(sel_plane=sel_plane)
        sub_vals = self.addrstates[sub_indices]
        sub_modes = self.addrmodes[sub_indices]

        mask = np.all(
            (sub_vals > ERASE) & (sub_vals >= _offset) & (sub_modes == mode), axis=1
        )

        blkadds = sub_indices[mask]

        if len(blkadds) == 0:
            return empty_arr()

        readadds = self.addrstates[blkadds]
        readadds -= _offset
        minvals = np.min(readadds, axis=1)

        arr_tot = []
        for i in range(blkadds.shape[0]):
            r = minvals[i] + 1
            Ai_repeat = np.repeat(blkadds[i : i + 1, :], r, axis=0)
            ri = np.repeat(np.arange(r).reshape(-1, 1), blkadds.shape[1], axis=1)
            combined = np.dstack([Ai_repeat, ri])
            arr_tot.extend(combined)

        return np.vstack(arr_tot) # shape: (?, len(sel_plane), 2)

    def get_erasable(self, sel_plane: int | list = None, force_new: bool = True):
        """
        addrErasable 반환
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        if force_new:
            self.addrErasable = self._get_erasable(sel_plane=sel_plane)
        return self.addrErasable

    def get_multi_erasable(self, sel_plane: list, force_new: bool = True):
        """
        multi addrErasable 반환
        """
        if force_new:
            self.addrErasable = self._get_multi_erasable(sel_plane=sel_plane)
        return self.addrErasable

    def get_pgmable(
        self, mode=TLC, sel_plane: int | list = None, force_new: bool = True
    ):
        """
        addrPGMable 반환
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        if force_new:
            self.addrPGMable = self._get_pgmable(mode=mode, sel_plane=sel_plane)
        return self.addrPGMable

    def get_multi_pgmable(self, sel_plane: list, mode=TLC, force_new: bool = True):
        """
        multi addrPGMable 반환
        """
        if force_new:
            self.addrPGMable = self._get_multi_pgmable(mode=mode, sel_plane=sel_plane)
        return self.addrPGMable

    def get_readable(
        self,
        offset: int = None,
        mode=TLC,
        sel_plane: int | list = None,
        force_new: bool = True,
    ):
        """
        addrReadable 반환
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        if force_new:
            self.addrReadable = self._get_readable(
                offset=offset, mode=mode, sel_plane=sel_plane
            )
        return self.addrReadable

    def get_multi_readable(
        self, sel_plane: list, offset: int = None, mode=TLC, force_new: bool = True
    ):
        """
        multi addrReadable 반환
        """
        if force_new:
            self.addrReadable = self._get_multi_readable(
                offset=offset, sel_plane=sel_plane, offset=offset, mode=mode
            )
        return self.addrReadable

    def sample_erasable(self, size: int = 1):
        """
        addrErasable 에서 size 만큼 샘플링
        """
        size_tot = len(self.addrErasable)
        if size > size_tot:
            self.oversample = True
            return self.addrErasable
        else:
            self.oversample = False
            idx = np.random.choice(size_tot, size=size, replace=False)
            return self.addrErasable[idx]

    def sample_pgmable(self, size: int = 1, sequential: bool = False):
        """
        addrPGMable 에서 size 만큼 샘플링
        """
        size_tot = len(self.addrPGMable)

        if not sequential:
            if size > size_tot:
                self.oversample = True
                return self.addrPGMable
            self.oversample = False
            idx = np.random.choice(size_tot, size=size, replace=False)
            return self.addrPGMable[idx]

        if size > size_tot:
            return empty_arr()

        _save_idx = np.random.choice(size_tot)
        for direction in (1, -1):
            idx = _save_idx
            while 0 <= idx <= size_tot - size if direction == 1 else idx >= 0:
                page = self.addrPGMable[idx, 0, 1]
                if page + size <= self.pagesize - 1:
                    arr_tot = []
                    arr_base = self.addrPGMable[idx : idx + 1]
                    for i in range(size):
                        arr = arr_base.copy()
                        arr[..., 1] = arr[..., 1] + i
                        arr_tot.extend(arr)

                    return np.stack(arr_tot, axis=0)

                idx += direction
        return empty_arr()

    def sample_readable(self, size: int = 1, sequential: bool = False):
        """
        addrReadable 에서 size 만큼 샘플링
        sequential : True 이면 연속된 주소를 샘플링, False 이면 랜덤 샘플링
        """
        size_tot = len(self.addrReadable)

        if not sequential:
            if size > size_tot:
                self.oversample = True
                return self.addrReadable
            self.oversample = False
            idx = np.random.choice(size_tot, size=size, replace=False)
            return self.addrReadable[idx]

        if size > size_tot:
            return empty_arr()

        _save_idx = np.random.choice(size_tot)
        for direction in (1, -1):
            idx = _save_idx
            while 0 <= idx <= size_tot - size if direction == 1 else idx >= 0:
                res = self.addrReadable[idx : idx + size]
                if len(res) < size:
                    break
                diff_page = np.diff(np.dstack(res))
                if np.all(diff_page[:, 0, :] == 0) and np.all(diff_page[:, 1, :] == 1):
                    return res
                idx += direction
        return empty_arr()

    def random_erase(self, sel_plane: int | list = None, mode=TLC, size: int = 1):
        """
        addrErasable 에서 size 만큼 랜덤 erase
        sel_plane : 선택된 plane 인덱스
        """
        if sel_plane is None or isinstance(sel_plane, int) or len(sel_plane) == 1:
            self.get_erasable(sel_plane=sel_plane)
        else:
            self.get_multi_erasable(sel_plane=sel_plane)

        sample = self.sample_erasable(size=size)
        res1 = self.set_adds_erase(sample, mode=mode)
        next = self.get_vals_adds(sample)
        res2 = not (np.all(next == ERASE))
        if res2:
            raise ValueError("at least one of addrstates is not erased")

        return res1

    def random_pgm(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        sequential: bool = False,
    ):
        """
        addrPGMable 에서 size 만큼 랜덤 pgm
        sel_plane : 선택된 plane 인덱스
        """
        if sel_plane is None or isinstance(sel_plane, int) or len(sel_plane) == 1:
            self.get_pgmable(sel_plane=sel_plane, mode=mode)
        else:
            self.get_multi_pgmable(sel_plane=sel_plane, mode=mode)

        sample = self.sample_pgmable(size=size, sequential=sequential)
        prev = self.get_vals_adds(sample)
        res1 = self.set_adds_pgm(sample, mode=mode)
        next = self.get_vals_adds(sample)
        res2 = not (np.all(prev != next))
        if res2:
            raise ValueError("at least one of addrstates did not changed")

        return res1

    def random_read(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        offset: int = None,
        sequential: bool = False,
    ):
        """
        addrReadable 에서 size 만큼 랜덤 read
        sel_plane : 선택된 plane 인덱스
        """
        if sel_plane is None or isinstance(sel_plane, int) or len(sel_plane) == 1:
            self.get_readable(sel_plane=sel_plane, mode=mode, offset=offset)
        else:
            self.get_multi_readable(sel_plane=sel_plane, mode=mode, offset=offset)

        sample = self.sample_readable(size=size, sequential=sequential)

        return sample

    def visual_seq_3d(self, seq: list, title="NAND Access Trajectory"):
        """
        Block 별 Erase, PGM, Read 동작 target address 를 추적함
        seq: (cmd_id, (plane, block, page))
        title: figure 의 title
        """
        block_traces = {}
        for t, (cmd_id, addr_vec) in enumerate(seq):
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}
                block = addr["block"]
                page = addr["page"]
                block_traces.setdefault(block, []).append((page, t, cmd_id))

        # Define a color palette
        colors = [
            "blue",
            "green",
            "red",
            "orange",
            "purple",
            "brown",
            "pink",
            "gray",
            "olive",
            "cyan",
        ]

        # Create color map based on unique command IDs
        color_map = {
            cmd_id: colors[i % len(colors)]
            for i, cmd_id in enumerate(CMD_VOCAB.values())
        }

        fig = plt.figure(figsize=(12, 8))
        ax = fig.add_subplot(111, projection="3d")
        for blk, pts in block_traces.items():
            if not pts:
                continue
            pts.sort(key=lambda x: x[1])
            pages, times, cmds = zip(*pts)
            xs = [blk] * len(pages)
            ax.plot(xs, pages, times, alpha=0.4)
            ax.scatter(xs, pages, times, c=[color_map[c] for c in cmds], marker="o")

        # Add legend
        legend_elements = [
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label=cmd,
                markerfacecolor=color_map[CMD_VOCAB[cmd]],
                markersize=8,
            )
            for cmd in CMD_VOCAB.keys()
        ]

        ax.legend(handles=legend_elements, title="Commands")
        ax.set_xlabel("Block")
        ax.set_ylabel("Page")
        ax.set_zlabel("Time")
        ax.set_title(title)
        plt.tight_layout()
        plt.show()

    def visual_seq_heatmap(
        self,
        seq: list,
        binned: bool = True,
        block_bins=100,
        page_bins=100,
        title="Address Heatmap",
    ):
        """
        Block, Address 별 address access 횟수를 누적하여 heatmap 생성
        seq: (cmd_id, (plane, block, page))
        binned: address range binning 여부
        block_bins: block bin 갯수
        page_bins: page bin 갯수
        title: figure 의 title
        """
        block_idxs = []
        page_idxs = []
        for cmd_id, addr_vec in seq:
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}

                block = addr["block"]
                page = addr["page"]
                if binned:
                    block = int(block / self.num_blocks * (block_bins - 1))
                    page = int(page / self.pagesize * (page_bins - 1))

                block_idxs.append(block)
                page_idxs.append(page)

        if binned:
            heatmap_array = np.zeros((block_bins, page_bins), dtype=int)
            xtiklabels = block_bins // 10
            ytiklabels = page_bins // 10
        else:
            heatmap_array = np.zeros((self.num_blocks, self.pagesize), dtype=int)
            xtiklabels = self.num_blocks // 10
            ytiklabels = self.pagesize // 10

        for b, p in zip(block_idxs, page_idxs):
            heatmap_array[b, p] += 1

        plt.figure(figsize=(10, 6))
        sns.heatmap(
            heatmap_array.T,
            cmap="Reds",
            cbar=True,
            xticklabels=xtiklabels,
            yticklabels=ytiklabels,
        )
        plt.title(title)
        plt.xlabel("Block")
        plt.ylabel("Page")
        plt.gca().invert_yaxis()
        plt.tight_layout()
        plt.show()

    def visual_freq_histograms(self, seq: list, title="Operation Frequency Histograms"):
        """
        Generate frequency histograms for commands, planes, blocks, and pages from sequence data
        seq: (cmd_id, (plane, block, page)) tuples
        title: figure title
        """
        # Extract all components from sequence
        cmds = []
        planes = []
        blocks = []
        pages = []

        for cmd_id, addr_vec in seq:
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}
                cmds.append(cmd_id)
                planes.append(addr["plane"])
                blocks.append(addr["block"])
                pages.append(addr["page"])

        # Create subplots
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        fig.subtilte(title, fontsize=16)

        # Command frequency histogram
        cmd_counts = {}
        for cmd in cmds:
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1

        cmd_names = list(CMD_VOCAB.keys())
        cmd_values = [cmd_counts.get(i, 0) for i in range(len(cmd_names))]

        axes[0, 0].bar(range(len(cmd_names)), cmd_values)
        axes[0, 0].set_xticks(range(len(cmd_names)))
        axes[0, 0].set_xticklabels(cmd_names)
        axes[0, 0].set_title("Command Frequency")
        axes[0, 0].set_ylabel("Frequency")

        # Plane frequency histogram
        plane_counts = {}
        for plane in planes:
            plane_counts[plane] = plane_counts.get(plane, 0) + 1

        plane_values = [plane_counts.get(i, 0) for i in range(self.num_planes)]

        plane_labels = list(range(self.num_planes))
        axes[0, 1].bar(range(len(plane_values)), plane_values)
        axes[0, 1].set_xticks(plane_labels)
        axes[0, 1].set_xticklabels(plane_labels)
        axes[0, 1].set_title("Plane Frequency")
        axes[0, 1].set_ylabel("Frequency")

        # Block frequency histogram
        block_counts = {}
        for block in blocks:
            block_counts[block] = block_counts.get(block, 0) + 1

        # Use a reasonable number of bins for blocks
        block_bins = min(50, len(block_counts))
        axes[1, 0].hist(blocks, bins=block_bins, edgecolor="black")
        axes[1, 0].set_title("Block Frequency")
        axes[1, 0].set_xlabel("Block Addr.")
        axes[1, 0].set_ylabel("Frequency")

        # Page frequency histogram
        page_bins = min(50, len(pages))
        axes[1, 1].hist(pages, bins=page_bins, edgecolor="black")
        axes[1, 1].set_title("Page Frequency")
        axes[1, 1].set_xlabel("Page Addr.")
        axes[1, 1].set_ylabel("Frequency")

        plt.tight_layout()
        plt.show()


# addrman 사용 예제
if 1 == 1:
    # device parameter 설정
    num_planes = 4
    num_blocks = 1020
    pagesize = 2564
    offset = 0

    num_samples = 1000
    test_mode = TLC
    p_init_erase = 0.5
    erased_blocks = int(p_init_erase * num_blocks)
    p_init_pgm = 0.001  # erase block 에서 pagesize 의 몇 퍼센트 pgm 할 지 확률

    # badblock 설정
    # badlist = np.random.choice(num_blocks, num_blocks*1//100)
    badlist = []

    # instance creation
    addman = AddressManager(
        num_planes=num_planes,
        num_blocks=num_blocks,
        pagesize=pagesize,
        offset=offset,
        init=GOOD,
        badlist=badlist,
    )

    # dict 초기화 : cmds, planes, modes
    dict_cmds = {i: 0 for i in ("ERASE", "PGM", "READ")}
    comb_planes = all_subsets(set(range(num_planes)))
    # print(f"plane combinations: {comb_planes}")
    dict_planes = {str(comb): 0 for comb in comb_planes}
    dict_modes = {mode: 0 for mode in (TLC, SLC)}

    # 항목별 확률 weight 설정
    p_opers = np.array([1, 5, 10], dtype=float)
    p_opers /= np.sum(p_opers)
    p_planes = np.ones(len(dict_planes), dtype=float) / len(dict_planes)
    # p_planes[:] = 0
    # p_planes[1] = 1 # plane 0: (0,)
    # p_planes[14] = 1 # plane 0~3: (0,1,2,3)
    p_planes /= np.sum(p_planes)
    p_modes = np.ones(len(dict_modes), dtype=float) / len(dict_modes)

    # 사전 erase
    cnt_tot = cnt = 0
    for _ in range(erased_blocks):
        adds = addman.random_erase(mode=test_mode)
        cnt_tot += 1
        if len(adds):
            cnt += 1

    states = addman.get_addrstates()
    modes = addman.get_addrmodes()
    print(
        f"pre erase succ rate: {cnt/cnt_tot:.2f}, total blocks: {num_blocks}, attempt:{cnt_tot}, success: {cnt}"
    )
    print(
        f"{test_mode} erased block rate: {np.sum((states == ERASE) & (modes == test_mode))/num_blocks}"
    )

    # 사전 pgm
    cnt_tot = cnt = 0
    for _ in range(int(erased_blocks * pagesize * p_init_pgm)):
        adds = addman.random_pgm(mode=test_mode)
        cnt_tot += 1
        if len(adds):
            cnt += 1

    print(
        f"pre pgm succ rate: {cnt/cnt_tot:.2f}, total blocks: {num_blocks}, attempt:{cnt_tot}, success: {cnt}"
    )
    print(
        f"{test_mode} pgmed block rate: {np.sum((states > ERASE) & (modes == test_mode))/num_blocks}"
    )

    # sampling 반복
    sequence = []
    cnt_tot = cnt = 0
    with open("output.txt", "w") as file:
        for i in range(num_samples):
            op = np.random.choice(list(CMD_VOCAB.keys()), p=p_opers)
            sel_plane = comb_planes[np.random.choice(len(comb_planes), p=p_planes)]
            # mode = np.random.choice(list(dict_modes.keys()), p=p_modes)
            mode = test_mode
            match op:
                case "ERASE":
                    adds = addman.random_erase(sel_plane=sel_plane, mode=mode)
                case "PGM":
                    adds = addman.random_pgm(sel_plane=sel_plane, mode=mode)
                    # adds = addman.random_pgm(sel_plane=sel_plane, mode=mode, size=20, sequential=True)
                case "READ":
                    adds = addman.random_read(sel_plane=sel_plane, mode=mode)
                    # adds = addman.random_read(sel_plane=sel_plane, mode=mode, size=20, sequential=True)
            if len(adds) == 0:
                file.write(
                    f"{i+1} rep, FAIL, {mode}, {op}, planes:{sel_plane}, addr:NONE\n"
                )
            else:
                str_adds = [e for e in np.squeeze(adds).tolist()]
                cnt += 1
                file.write(
                    f"{i+1} rep, SUCC, {mode}, {op}, planes:{sel_plane}, addr:{str_adds}\n"
                )

                cmd_id = CMD_VOCAB[op.item()]
                blocks = adds[..., 0].flatten()
                planes = blocks % addman.num_planes
                pages = adds[..., 1].flatten()
                # print(cmd_id, op)

                seq = list(zip(planes.tolist(), blocks.tolist(), pages.tolist()))
                sequence.append((cmd_id, seq))

            cnt_tot += 1

    print(f"operation succ rate: {cnt/cnt_tot:.2f}, attempt:{cnt_tot}, success: {cnt}")

    # 시각화 출력
    addman.visual_seq_3d(sequence)
    addman.visual_seq_heatmap(sequence)
    addman.visual_freq_histograms(sequence)

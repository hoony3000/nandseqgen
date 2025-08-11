# address_manager.py (스텁)
class AddressManager:
    def __init__(self, cfg):
        self.cfg=cfg
        # 간이 상태 테이블 초기화
    def available_at(self, die, plane)->float:
        return 0.0
    def select(self, kind)->list:
        # address_policy 반영해서 임의 주소 반환(스텁)
        return []
    def precheck(self, kind, targets)->bool:
        return True
    def reserve(self, targets): 
        pass
    def commit(self, targets, kind):
        pass
    def observe_states(self, die, plane):
        # 전역/로컬 상태 버킷화 결과 반환
        return (
            {"pgmable_ratio":"mid","readable_ratio":"mid"}, 
            {"plane_busy_frac":"low"}
        )
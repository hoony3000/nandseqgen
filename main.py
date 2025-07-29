from gen_seq import *
import yaml

if __name__ == "__main__":
  TimeSpec = {}
  ExecTime = {}
  StateOperWeight = {}
  
  # yaml 파일 load 하여 AC timing load
  with open('config.yaml', 'r', encoding='utf-8') as file:
    Dict = yaml.safe_load(file)
    TimeSpec = Dict['TimeSpec']
    ExecTime = Dict['ExecTime']
  
  # TimeSpec 정의 (수정중)
  tWB                        = StateSeq.create(times=TimeSpec['tWB']             ,states='tWB'                        )
  tERS_TLC                   = StateSeq.create(times=TimeSpec['tERS_TLC']        ,states='erase TLC busy'             )
  tPROGO                     = StateSeq.create(times=TimeSpec['tPROGO']          ,states='pgm TLC busy'               )
  tTRAN_L                    = StateSeq.create(times=TimeSpec['tTRAN']           ,states='pgm LSB transfer busy'      )
  tTRAN_C                    = StateSeq.create(times=TimeSpec['tTRAN']           ,states='pgm CSB transfer busy'      )
  tTRAN_M                    = StateSeq.create(times=TimeSpec['tTRAN']           ,states='pgm MSB transfer busy'      )
  tR_SLC                     = StateSeq.create(times=TimeSpec['tR_SLC']          ,states='read SLC busy'              )
  tR_LSB                     = StateSeq.create(times=TimeSpec['tR_LSB']          ,states='read TLC busy'              )
  tR_CSB                     = StateSeq.create(times=TimeSpec['tR_CSB']          ,states='read TLC busy'              )
  tR_MSB                     = StateSeq.create(times=TimeSpec['tR_MSB']          ,states='read TLC busy'              )
  tRRC_SLC                   = StateSeq.create(times=TimeSpec['tRRC']            ,states='read SLC extready'          )
  tRRC_FWSLC                 = StateSeq.create(times=TimeSpec['tRRC']            ,states='read FWSLC extready'        )
  tRRC_TLC                   = StateSeq.create(times=TimeSpec['tRRC']            ,states='read TLC extready'          )
  tRST_ready                 = StateSeq.create(times=TimeSpec['tRST_ready']      ,states='reset busy ready'           )
  tRST_read                  = StateSeq.create(times=TimeSpec['tRST_read']       ,states='reset busy read'            )
  tRST_pgm                   = StateSeq.create(times=TimeSpec['tRST_pgm']        ,states='reset busy pgm'             )
  tERSL_TLC                  = StateSeq.create(times=TimeSpec['tRST_pgm']        ,states='erase TLC suspend busy'     )
  tPGMSL_TLC                 = StateSeq.create(times=TimeSpec['tRST_ready']      ,states='program TLC suspend busy'   )
  
  # operations 정의 (수정중)
  # seq[0] : operation 의 execution time -> 추후 dictionary data 로 입력을 받을 예정
  name='erase TLC'            ; erase_TLC                = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tWB + tERS_TLC              + End(name)          ,applyto='die'    )
  name='pgm LSB'              ; pgm_LSB                  = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='pgm CSB'              ; pgm_CSB                  = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='pgm MSB'              ; pgm_MSB                  = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='pgm MSB exec'         ; pgm_MSB_exec             = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='read LSB'             ; read_LSB                 = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tR_LSB + tRRC_TLC           + End('read TLC')    ,applyto='die'    )
  name='read CSB'             ; read_CSB                 = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tR_CSB + tRRC_TLC           + End('read TLC')    ,applyto='die'    )
  name='read MSB'             ; read_MSB                 = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tR_MSB + tRRC_TLC           + End('read TLC')    ,applyto='die'    )
  name='dout 06h'             ; dout_06h                 = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='SR 70h'               ; SR_70h                   = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='SR 78h'               ; SR_78h                   = Operation.create(name=name  ,seq=Nop(ExecTime[name])                               + End(name)          ,applyto='die'    )
  name='reset FAh'            ; reset_FAh                = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tRST_ready                  + End(name)          ,applyto='die'    )
  name='erase TLC suspend'    ; erase_TLC_suspend        = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tERSL_TLC                   + End(name)          ,applyto='die'    )
  name='erase TLC resume'     ; erase_TLC_resume         = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tERS_TLC.shift_time(-2*MS)  + End(name)          ,applyto='die'    )
  name='program TLC suspend'  ; program_TLC_suspend      = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tERSL_TLC                   + End(name)          ,applyto='die'    )
  name='program TLC resume'   ; program_TLC_resume       = Operation.create(name=name  ,seq=Nop(ExecTime[name]) + tPROGO.shift_time(-300*US)  + End(name)          ,applyto='die'    )
  
  # scheduler 동작 순서 예시
  if 1 == 1:
    sched = NANDScheduler(num_die=2, num_plane=4)
    len_op = Operation.len_class()
    id = np.random.choice(len_op, size=1, replace=True).tolist()[0]
    op = Operation.get_by_id(id)
    sched.setnow(0, 0, op)
    sched.stat(-1, -1)

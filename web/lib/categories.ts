// arXiv 분류 코드를 사람이 읽는 이름으로. 코드만 보이면 "cs.CY"가 무엇인지 알 수 없습니다(ADR-109).
// 이름은 arXiv 분류표(https://arxiv.org/category_taxonomy)를 우리말로 옮긴 것입니다. 목록에 없는 코드는 코드 그대로 둡니다.

export const CATEGORY_NAME: Record<string, string> = {
  "cs.AI": "인공지능",
  "cs.CL": "자연어 처리",
  "cs.LG": "기계학습",
  "cs.CV": "컴퓨터 비전",
  "cs.RO": "로봇공학",
  "cs.SE": "소프트웨어 공학",
  "cs.IR": "정보 검색",
  "cs.CR": "보안·암호",
  "cs.HC": "인간-컴퓨터 상호작용",
  "cs.CY": "컴퓨터와 사회",
  "cs.MA": "다중 에이전트",
  "cs.NE": "신경·진화 계산",
  "cs.DB": "데이터베이스",
  "cs.DC": "분산·병렬 컴퓨팅",
  "cs.DS": "자료구조·알고리즘",
  "cs.GT": "게임 이론",
  "cs.LO": "논리",
  "cs.SI": "사회·정보 네트워크",
  "cs.SD": "소리",
  "cs.NI": "네트워크",
  "cs.PL": "프로그래밍 언어",
  "cs.CE": "계산 공학",
  "cs.ET": "신기술",
  "cs.MM": "멀티미디어",
  "cs.GR": "그래픽스",
  "stat.ML": "통계적 기계학습",
  "eess.AS": "음성·음향 처리",
  "eess.IV": "영상 처리",
  "eess.SP": "신호 처리",
  "eess.SY": "시스템·제어",
  "math.OC": "최적화·제어",
  "q-bio.NC": "신경과학",
};

export const categoryName = (code: string) => CATEGORY_NAME[code] ?? code;

/** "인공지능 (cs.AI)". 이름이 없으면 코드만. */
export const categoryLabel = (code: string) => (CATEGORY_NAME[code] ? `${CATEGORY_NAME[code]} (${code})` : code);


import json
import math
import os
import random
import re
import threading
import time

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from dotenv import dotenv_values

load_dotenv()  # 이 폴더 또는 위쪽 폴더의 .env를 읽는다

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def find_api_key():
    """OPENAI_API_KEY를 찾는다. '여기에_키_입력' 같은 자리표시자는 건너뛰고 위쪽 폴더의 .env까지 찾아본다."""
    def ok(k):
        return bool(k) and k.strip().startswith("sk-") and k.isascii()
    key = os.getenv("OPENAI_API_KEY", "")
    if ok(key):
        return key.strip(), "환경변수"
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        path = os.path.join(d, ".env")
        if os.path.exists(path):
            k = dotenv_values(path).get("OPENAI_API_KEY", "") or ""
            if ok(k):
                return k.strip(), path
        d = os.path.dirname(d)
    return None, None


API_KEY, KEY_SOURCE = find_api_key()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=API_KEY, timeout=20.0, max_retries=1) if (OpenAI and API_KEY) else None
AI_STATUS = {"ok": None, "error": None if client else ("openai 패키지가 없습니다" if not OpenAI else "API 키를 찾지 못했습니다")}

app = Flask(__name__)
app.json.sort_keys = False

BRAND = "풍문투자증권"

# =================================================================
# 업종 성향 (현실 고증)
#   beta     : 시장 전체 움직임을 얼마나 따라가나 (1 = 시장과 같이)
#   vol      : 평소 출렁임
#   sens     : 찌라시/뉴스에 대한 민감도
#   momentum : 오르면 더 오르는 추세 추종 성향 (테마주일수록 높음)
#   revert   : 많이 움직이면 제자리로 돌아오려는 성향 (방어주일수록 높음)
#   skew     : 악재에 더 크게 반응하면 음수, 호재에 더 크게 반응하면 양수
#   hot      : 이 업종에서 주가를 크게 움직이는 단어 (찌라시 판정에 사용)
#   desc     : AI에게 알려 주는 업종 설명
# =================================================================
SECTORS = {
    "반도체": {"beta": 1.15, "vol": 0.9, "sens": 0.7, "momentum": 0.3, "revert": 0.2, "skew": 0.0,
             "hot": ["수주", "HBM", "파운드리", "수출", "공급 계약", "빅테크", "엔비디아", "감산", "재고"],
             "desc": "시가총액이 큰 대형주라 찌라시 한두 개로는 잘 안 움직인다. 글로벌 업황, 수출 실적, 대형 수주에 반응한다."},
    "바이오": {"beta": 0.8, "vol": 2.2, "sens": 1.6, "momentum": 0.5, "revert": 0.1, "skew": -0.1,
             "hot": ["임상", "FDA", "기술수출", "신약", "허가", "3상", "2상", "라이선스", "유상증자"],
             "desc": "임상 결과 하나에 상한가와 하한가를 오가는 대표적인 고변동 업종. 개인 투자자 비중이 높아 소문에 가장 민감하다."},
    "식품": {"beta": 0.45, "vol": 0.55, "sens": 0.55, "momentum": 0.0, "revert": 0.6, "skew": 0.0,
           "hot": ["가격 인상", "수출", "K-푸드", "원가", "곡물", "식중독", "리콜"],
           "desc": "경기와 상관없이 실적이 꾸준한 방어주. 웬만한 소문에는 덜 움직이고, 움직여도 금방 제자리로 돌아온다."},
    "전기차": {"beta": 1.4, "vol": 1.6, "sens": 1.3, "momentum": 0.9, "revert": 0.05, "skew": 0.1,
            "hot": ["보조금", "배터리", "자율주행", "테슬라", "수주", "판매량", "리콜", "화재"],
            "desc": "대표적인 테마주. 한번 불붙으면 추세가 길게 이어지고, 시장 분위기를 크게 탄다."},
    "건설": {"beta": 1.0, "vol": 1.1, "sens": 1.0, "momentum": 0.2, "revert": 0.3, "skew": -0.35,
           "hot": ["금리", "PF", "부동산", "재건축", "해외 수주", "미분양", "부도", "워크아웃"],
           "desc": "금리와 부동산 경기에 민감하다. 특히 PF 부실, 미분양 같은 악재에 훨씬 크게 반응한다."},
    "엔터": {"beta": 0.9, "vol": 1.7, "sens": 1.4, "momentum": 0.4, "revert": 0.2, "skew": 0.0,
           "hot": ["컴백", "월드투어", "열애설", "군입대", "탈퇴", "음원", "빌보드", "계약 해지"],
           "desc": "간판 아티스트의 컴백, 열애설, 군입대, 계약 이슈에 크게 출렁인다."},
    "항공": {"beta": 1.05, "vol": 1.2, "sens": 0.9, "momentum": 0.2, "revert": 0.3, "skew": -0.1,
           "hot": ["유가", "환율", "노선", "여객", "합병", "운항 중단", "사고"],
           "desc": "유가가 오르거나 원화가 약해지면(환율 상승) 비용이 늘어 주가가 빠진다. 여행 수요에도 민감하다."},
    "게임": {"beta": 1.1, "vol": 1.5, "sens": 1.2, "momentum": 0.6, "revert": 0.2, "skew": 0.0,
           "hot": ["신작", "출시", "판호", "흥행", "매출 순위", "확률형", "규제", "출시 연기"],
           "desc": "신작 기대감에 오르다가 출시 직후 떨어지는 경우가 많다. 중국 판호, 규제 뉴스에 민감하다."},
}

# 발행주식수(shares)는 게임용으로 줄인 값 (시가총액과 인수 비용 계산에 사용)
COMPANIES = {
    "HBE": {"name": "한빛전자", "sector": "반도체", "price": 52000, "shares": 120_000},
    "DDB": {"name": "도담바이오", "sector": "바이오", "price": 31000, "shares": 40_000},
    "SRF": {"name": "새롬푸드", "sector": "식품", "price": 18000, "shares": 25_000},
    "NRM": {"name": "누리모빌리티", "sector": "전기차", "price": 74000, "shares": 50_000},
    "GRC": {"name": "가람건설", "sector": "건설", "price": 9800, "shares": 30_000},
    "BYE": {"name": "별빛엔터", "sector": "엔터", "price": 42500, "shares": 20_000},
    "HNA": {"name": "하늘항공", "sector": "항공", "price": 23700, "shares": 60_000},
    "MAG": {"name": "모아게임즈", "sector": "게임", "price": 61200, "shares": 15_000},
}

# =================================================================
# 자산 레벨 (총자산 기준)
# =================================================================
LEVELS = [
    (0, "개미", "기본"),
    (15_000_000, "투자 동호회장", "암시장 아이템 10% 할인"),
    (30_000_000, "전업 투자자", "매매 수수료 면제"),
    (70_000_000, "슈퍼개미", "금감원 과징금 30% 감면"),
    (150_000_000, "큰손", "기업 경영권 인수 해금"),
    (500_000_000, "사모펀드 대표", "인수 기업 배당 2배"),
    (2_000_000_000, "재벌 총수", "인수 기업 소문이 '공시'로 통함 (믿을 확률 +25%p)"),
    (10_000_000_000, "회장님", "명예의 전당"),
]

# =================================================================
# 직업
#   unlock : 이 레벨에 처음 도달하면 선택지가 열린다
#   mods   : 게임 수치 보정
#     salary(1분마다 받는 돈) believe(믿을 확률 +) amp(찌라시 효과 ×) inv(조사 위험 +)
#     fan(팔로워 증가 ×) fan_loss(팔로워 이탈 ×) fan_power(팔로워 영향력 ×) fine(과징금 ×)
#     fee_free(수수료 면제) limit_fill(상·하한가 체결 실패 확률) div(배당 ×)
#     premium(인수 프리미엄) block(매각 할인) ad(팔로워 1명당 1분 광고 수익)
#     preview(시장 뉴스 미리 듣기) policy(정책 발표) lose_job(적발 시 직업 박탈)
# =================================================================
JOBS = {
    "office": {"name": "회사원", "icon": "briefcase", "unlock": 1,
               "pros": ["1분마다 월급 20만 원"], "cons": ["근무 중이라 찌라시 효과 -10%"],
               "mods": {"salary": 200_000, "amp": 0.9}},
    "jobless": {"name": "백수", "icon": "sofa", "unlock": 1,
                "pros": ["커뮤니티 상주: 팔로워 증가 ×1.3", "낼 돈이 없어 과징금 50% 감면"], "cons": ["수입 없음"],
                "mods": {"fan": 1.3, "fine": 0.5}},
    "student": {"name": "대학생", "icon": "graduation", "unlock": 1,
                "pros": ["알바비 1분마다 5만 원", "학교 커뮤니티: 팔로워 증가 ×1.6"], "cons": ["어린 친구 말이라 믿을 확률 -5%p"],
                "mods": {"salary": 50_000, "fan": 1.6, "believe": -0.05}},
    "civil": {"name": "공무원", "icon": "landmark", "unlock": 1,
              "pros": ["1분마다 월급 25만 원", "설마 공무원이? 조사 위험 -5%p"], "cons": ["적발 시 징계까지: 과징금 ×1.5"],
              "mods": {"salary": 250_000, "inv": -0.05, "fine": 1.5}},
    "analyst": {"name": "증권사 애널리스트", "icon": "line-chart", "unlock": 3,
                "pros": ["1분마다 급여 40만 원", "리포트의 권위: 믿을 확률 +15%p"], "cons": ["준법감시: 조사 위험 +5%p"],
                "mods": {"salary": 400_000, "believe": 0.15, "inv": 0.05}},
    "youtuber": {"name": "주식 유튜버", "icon": "video", "unlock": 3,
                 "pros": ["팔로워 증가 ×2.5, 영향력 ×2", "구독자 1명당 1분마다 광고 수익 30원"], "cons": ["헛발질하면 구독 취소 ×1.5"],
                 "mods": {"fan": 2.5, "fan_power": 2.0, "ad": 30, "fan_loss": 1.5}},
    "reporter": {"name": "경제부 기자", "icon": "newspaper", "unlock": 4,
                 "pros": ["1분마다 급여 30만 원", "단독 보도: 믿을 확률 +20%p, 효과 +10%"], "cons": ["언론 플레이 감시: 조사 위험 +8%p"],
                 "mods": {"salary": 300_000, "believe": 0.20, "amp": 1.1, "inv": 0.08}},
    "pro": {"name": "전문투자자", "icon": "trending-up", "unlock": 4,
            "pros": ["매매 수수료 면제", "상·하한가에서도 체결이 잘 된다"], "cons": ["월급 없음, 실력으로만 번다"],
            "mods": {"fee_free": True, "limit_fill": 0.25}},
    "pe": {"name": "사모펀드 사장", "icon": "building", "unlock": 5,
           "pros": ["경영권 프리미엄 20% → 5%", "블록딜 할인 없음, 배당 ×1.5"], "cons": ["큰손 감시: 조사 위험 +5%p"],
           "mods": {"premium": 0.05, "block": 0.0, "div": 1.5, "inv": 0.05}},
    "heir": {"name": "재벌 3세", "icon": "crown", "unlock": 6,
             "pros": ["1분마다 용돈 500만 원", "법무팀: 과징금 50% 감면"], "cons": ["금수저 말은 안 믿는다: 믿을 확률 -10%p"],
             "mods": {"salary": 5_000_000, "fine": 0.5, "believe": -0.10}},
    "lawmaker": {"name": "국회의원", "icon": "landmark", "unlock": 6,
                 "pros": ["1분마다 세비 60만 원", "시장 뉴스를 10초 먼저 듣는다", "불체포 특권: 조사 위험 -15%p"], "cons": ["적발되면 의원직 상실"],
                 "mods": {"salary": 600_000, "preview": True, "inv": -0.15, "lose_job": True}},
    "president": {"name": "대통령", "icon": "flag", "unlock": 7,
                  "pros": ["1분마다 급여 100만 원", "정책을 직접 발표해 시장을 움직인다 (1분에 1번)", "조사 위험 -30%p"], "cons": ["적발되면 탄핵"],
                  "mods": {"salary": 1_000_000, "policy": True, "inv": -0.30, "lose_job": True}},
}
SALARY_TICKS = 120   # 월급·광고 수익 주기 = 1분

# 찌라시 대결
DUEL_FIRST = (30, 60)       # 첫 대결 신청까지 (틱) = 약 15~30초
DUEL_GAP = (70, 130)        # 다음 대결까지 = 약 35~65초
DUEL_OFFER_TICKS = 40       # 수락 대기 = 20초
DUEL_FIGHT_TICKS = 90       # 반박 작성 시간 = 45초
DUEL_RIVALS = [  # (등장 레벨, 이름)
    (1, "동네 개미 '김부장'"), (2, "단톡방 '주식요정'"), (3, "리딩방 '여의도 고수'"), (4, "주식 유튜버 '떡상맨'"),
    (5, "슈퍼개미 '동학'"), (6, "작전세력 '청담 큰손'"), (7, "외국계 헤지펀드 '블랙스완'"), (8, "전설의 작전주포 '무명씨'"),
]
POLICY_COOLDOWN = 120


def job_of(st):
    return JOBS.get(st["job"]) if st["job"] else None


def mod(st, key, default):
    j = job_of(st)
    return j["mods"].get(key, default) if j else default


# =================================================================
# 난이도: 레벨(현재 총자산)이 높을수록 시장 감시가 세진다
#   (이름, 믿을 확률 보정, 조사 위험 보정, 역풍 배율, 대결 상대 실력)
# =================================================================
DIFFICULTY = {
    1: ("쉬움", 0.15, 0.00, 1.00, 45), 2: ("쉬움", 0.10, 0.01, 1.05, 50),
    3: ("보통", 0.05, 0.02, 1.10, 55), 4: ("보통", 0.00, 0.03, 1.15, 60),
    5: ("어려움", -0.05, 0.05, 1.25, 65), 6: ("어려움", -0.10, 0.07, 1.35, 70),
    7: ("지옥", -0.15, 0.09, 1.45, 76), 8: ("지옥", -0.20, 0.12, 1.60, 82),
}


def difficulty(st):
    return DIFFICULTY[level_of(total_assets(st))]


# 영향력 등급 (팔로워 기준)
TIERS = [
    (0, "무명"), (100, "동네 고수"), (1_000, "리딩방 방장"), (10_000, "여의도 스타"), (50_000, "전설의 작전주포"),
]

# =================================================================
# 시장 / 밸런스
# =================================================================
START_CASH = 10_000_000
TICK_SEC = 0.5
DAY_TICKS = 720                  # 한 거래일 = 720틱 = 실제 6분 (09:00~15:30)
MARKET_SEC_PER_TICK = 23400 / DAY_TICKS   # 게임 시계는 한 틱에 32.5초씩 흐른다
DAY = {"start": 0}               # 오늘 장이 시작된 틱
PRESSURE_DECAY = 0.94
SECTOR_DECAY = 0.93
VOL_DECAY = 0.96
HISTORY_MAX = 600
PRICE_LIMIT = 0.30               # 상한가/하한가 ±30%
NPC_RUMOR_TICKS = (24, 70)
MACRO_TICKS = (60, 140)          # 시장 뉴스 간격 = 약 30~70초
INVESTIGATION_DELAY = (16, 40)
SURGE_WINDOW, SURGE_PCT, SURGE_COOLDOWN = 10, 0.05, 60
DIVIDEND_TICKS = 120             # 인수 기업 배당 주기 = 1분
STAKE_RATIO = 0.30               # 경영권 인수 지분 30%
TAKEOVER_PREMIUM = 0.20          # 경영권 프리미엄 20%
BLOCK_DISCOUNT = 0.05            # 매각 시 블록딜 할인 5%
FEE_RATE = 0.00015               # 매매 수수료 0.015%
TAX_RATE = 0.0018                # 매도 시 증권거래세 0.18%
BANKRUPT_LINE = 300_000
# 공매도
SHORT_MARGIN = 0.40              # 공매도할 때 현금에서 묶이는 증거금 (매도 금액의 40%)
SHORT_CALL = 1.15                # 담보(매도 대금+증거금)가 상환 비용의 115% 밑이면 반대매매
BORROW_RATE = 0.001              # 대차 수수료: 1분마다 빌린 주식 가치의 0.1%          # 총자산이 이 밑으로 떨어지면 파산
START_FOLLOWERS = 12

NPC_TYPES = [
    ("개미", 0.55, (1, 30)), ("슈퍼개미", 0.20, (30, 150)), ("외국인", 0.12, (50, 400)),
    ("기관", 0.10, (80, 500)), ("큰손", 0.03, (300, 1500)),
]

ITEMS = {
    "reporter": {"name": "기자 섭외", "icon": "newspaper", "price": 300_000, "kind": "rumor",
                 "desc": "시장이 믿을 확률 +25%p"},
    "megaphone": {"name": "리딩방 확성기", "icon": "megaphone", "price": 500_000, "kind": "rumor",
                  "desc": "효과 ×1.5, 조사 위험 증가"},
    "lawyer": {"name": "변호사 선임", "icon": "scale", "price": 800_000, "kind": "passive",
               "desc": "금감원 조사 1회 방어 (자동)"},
    "wash": {"name": "평판 세탁", "icon": "sparkles", "price": 400_000, "kind": "instant",
             "desc": "찌라시 신뢰도 즉시 +15"},
}

# 업종 맞춤 NPC 찌라시
SECTOR_RUMORS = {
    "반도체": {"good": ["{name}, 북미 빅테크와 HBM {n}조 원 공급 계약 임박설", "{name}, 파운드리 대형 고객사 확보했다더라"],
             "bad": ["{name}, 주력 메모리 재고 {n}개월치 쌓였다는 얘기", "{name}, 수출 규제 대상에 포함될 수 있다던데"]},
    "바이오": {"good": ["{name}, 신약 임상 3상 성공적이라는 내부 소식", "{name}, 글로벌 제약사에 {n}조 원 기술수출 협상 중"],
             "bad": ["{name}, 임상 2상 데이터 기대 이하라는 소문", "{name}, 운영자금 부족으로 대규모 유상증자 검토설"]},
    "식품": {"good": ["{name}, 미국 대형마트 K-푸드 독점 납품 계약설", "{name}, 다음 달 주력 제품 가격 {n}% 인상 예정"],
           "bad": ["{name}, 공장 위생 문제로 식약처 점검 받는다던데", "{name}, 곡물가 급등으로 원가 부담 심각하다더라"]},
    "전기차": {"good": ["{name}, 신형 배터리 주행거리 {n}00km 돌파설", "{name}, 테슬라 부품 공급망 진입 임박"],
            "bad": ["{name}, 신차 배터리 화재로 리콜 검토 중이라는 소문", "{name}, 내년 보조금 대상에서 빠질 수 있다던데"]},
    "건설": {"good": ["{name}, 중동 {n}조 원 해외 수주 막바지 협상", "{name}, 강남 재건축 시공사 선정 유력"],
           "bad": ["{name}, PF 우발채무 {n}천억 원 숨겨져 있다는 얘기", "{name}, 지방 사업장 미분양 심각, 워크아웃설"]},
    "엔터": {"good": ["{name}, 간판 그룹 월드투어 {n}0회 추가 확정설", "{name}, 신인 그룹 데뷔곡 빌보드 진입 유력"],
           "bad": ["{name}, 간판 멤버 열애설 터지기 직전이라던데", "{name}, 메인 그룹 재계약 불발, 탈퇴설"]},
    "항공": {"good": ["{name}, 유럽 신규 노선 {n}개 취항 허가 임박", "{name}, 경쟁사 합병 무산으로 반사이익 기대"],
           "bad": ["{name}, 기체 결함으로 일부 노선 운항 중단 검토", "{name}, 유류할증료 못 올려 적자 폭 커질 거라더라"]},
    "게임": {"good": ["{name}, 신작 중국 판호 발급 확정됐다더라", "{name}, 신작 사전예약 {n}00만 명 돌파 임박"],
           "bad": ["{name}, 기대 신작 출시 연기 확정이라는 소문", "{name}, 확률형 아이템 조작 의혹으로 조사 들어간다던데"]},
}
WEIRD_RUMORS = ["{name} 회장님이 꿈에서 다음 주 주가를 봤다더라", "{name} 본사 지하에서 금광이 발견됐다고?",
                "{name}, 화성 이주 프로젝트 공식 파트너 선정설"]
NPC_AUTHORS = ["슈퍼개미 '동학'", "리딩방 '여의도 고수'", "외국계 작전세력", "익명 증권가 메신저", "기관 내부자(?)", "주식 유튜버 '떡상맨'"]

# 시장 뉴스(매크로): market = 시장 전체 영향, sectors = 업종별 영향, idx = 지표 변화
MACRO_EVENTS = [
    {"text": "한국은행, 기준금리 0.25%p 전격 인하", "market": 0.010, "sectors": {"건설": 0.05, "바이오": 0.025, "게임": 0.015}, "idx": {"rate": -0.25}, "kw": ["금리 인하", "한국은행"]},
    {"text": "한국은행, 물가 잡으려 기준금리 0.25%p 인상", "market": -0.010, "sectors": {"건설": -0.05, "바이오": -0.025}, "idx": {"rate": 0.25}, "kw": ["금리 인상"]},
    {"text": "중동 긴장 고조, 국제유가 배럴당 10달러 급등", "market": -0.006, "sectors": {"항공": -0.06, "식품": -0.015}, "idx": {"oil": 10}, "kw": ["국제유가", "중동"]},
    {"text": "산유국 증산 합의, 국제유가 급락", "market": 0.004, "sectors": {"항공": 0.05}, "idx": {"oil": -9}, "kw": ["유가 급락"]},
    {"text": "원/달러 환율 1,450원 돌파, 수출주 강세", "market": -0.003, "sectors": {"반도체": 0.03, "항공": -0.04, "식품": -0.01}, "idx": {"fx": 45}, "kw": ["환율", "수출주"]},
    {"text": "미국, 대중 반도체 수출 규제 일부 완화", "market": 0.008, "sectors": {"반도체": 0.06}, "idx": {}, "kw": ["반도체 규제", "수출 규제"]},
    {"text": "정부, 내년 전기차 보조금 대폭 축소 발표", "market": -0.002, "sectors": {"전기차": -0.07}, "idx": {}, "kw": ["전기차 보조금"]},
    {"text": "美 FDA, 신약 허가 심사 기준 강화", "market": -0.002, "sectors": {"바이오": -0.06}, "idx": {}, "kw": ["FDA", "신약 허가"]},
    {"text": "K-팝 글로벌 투어 매출 역대 최대 기록", "market": 0.002, "sectors": {"엔터": 0.05}, "idx": {}, "kw": ["K-팝", "월드투어"]},
    {"text": "중국, 한국 게임 판호 무더기 발급", "market": 0.003, "sectors": {"게임": 0.06}, "idx": {}, "kw": ["판호"]},
    {"text": "부동산 PF 부실 우려 재점화, 건설사 신용등급 하향", "market": -0.006, "sectors": {"건설": -0.07}, "idx": {}, "kw": ["PF 부실", "신용등급"]},
    {"text": "외국인, 코스피 1조 원 대규모 순매수", "market": 0.015, "sectors": {"반도체": 0.01}, "idx": {}, "kw": ["외국인 순매수"]},
    {"text": "미국 증시 급락에 공포지수 급등", "market": -0.020, "sectors": {"전기차": -0.02, "바이오": -0.015}, "idx": {"fx": 15}, "kw": ["미국 증시", "공포지수"]},
    {"text": "국제 곡물가 하락으로 식품업계 원가 부담 완화", "market": 0.001, "sectors": {"식품": 0.04}, "idx": {}, "kw": ["곡물가"]},
    {"text": "해외여행 수요 폭발, 항공권 예약 사상 최대", "market": 0.002, "sectors": {"항공": 0.045}, "idx": {}, "kw": ["해외여행", "항공권"]},
]

GOOD_WORDS = ["계약", "수주", "인수", "흑자", "승인", "대박", "신제품", "투자 유치", "협력", "독점", "특허", "최대", "돌파", "상승", "성공", "확정", "허가", "판호", "컴백", "취항", "흥행", "수출", "가격 인상", "급등", "월드투어"]
BAD_WORDS = ["적자", "소송", "리콜", "횡령", "압수수색", "부도", "실패", "취소", "유출", "사고", "조사", "하락", "결함", "철수", "연기", "열애설", "탈퇴", "유상증자", "워크아웃", "화재", "우발채무", "부실", "미분양", "규제", "숨겨", "급락", "중단"]

BASE_KEYWORDS = ["금리", "환율", "반도체 업황", "2차전지", "공매도", "배당주", "AI 반도체", "공모주", "유가", "코스피"]
STOPWORDS = {"대상", "주가", "그리고", "근데", "그런데", "진짜", "완전", "이번", "다음", "오늘", "내일", "소문", "얘기", "정도", "관련", "예정", "발표", "규모", "가능성", "내부", "소식"}
JOSA = ("에서는", "이라던데", "라던데", "다던데", "한다더라", "이라고", "에서", "으로", "이랑", "한테", "까지", "부터", "처럼", "보다", "라고",
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "와", "과", "만", "설", "래", "대", "요")

LOCK = threading.Lock()


# =================================================================
# 유틸
# =================================================================
def clamp(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return max(lo, min(hi, v))


def tick_size(p):
    """한국 주식시장 호가 단위"""
    for limit, unit in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if p < limit:
            return unit
    return 1000


def round_price(p):
    u = tick_size(p)
    return max(u, int(round(p / u)) * u)


def market_clock(ticks):
    sec = int(9 * 3600 + ((ticks - DAY["start"]) % DAY_TICKS) * MARKET_SEC_PER_TICK)
    return f"{sec // 3600 % 24:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"


def stake_value(st):
    return sum(v["shares"] * st["stocks"][c]["price"] for c, v in st["stakes"].items())


def cash_move(st, amount, kind, desc, code=None):
    """현금이 움직이는 모든 경우는 여기를 거친다 → 입출금 내역에 잔액과 함께 남는다"""
    amount = int(round(amount))
    st["cash"] += amount
    st["seq"] += 1
    st["cashlog"].insert(0, {"id": st["seq"], "day": st.get("day", 1), "clock": market_clock(st["ticks"]), "kind": kind,
                             "desc": desc, "code": code, "amount": amount, "balance": st["cash"]})
    del st["cashlog"][600:]


def cover_short(st, code, q, forced=False):
    """공매도 상환: q주를 지금 가격에 사서 갚는다. 실현손익 = 담보로 묶인 매도 대금(비례분) − 상환 비용"""
    s, v = st["stocks"][code], st["shorts"][code]
    q = min(q, v["qty"])
    price = s["price"]
    amount = price * q
    fee = 0 if (has_perk(st, 3) or mod(st, "fee_free", False)) else int(amount * FEE_RATE)
    # 담보는 원 단위 정수로 비례 배분 (전량 상환이면 남은 금액 전부)
    if q == v["qty"]:
        proceeds_part, margin_part = v["proceeds"], v["margin"]
    else:
        proceeds_part = int(round(v["proceeds"] * q / v["qty"]))
        margin_part = int(round(v["margin"] * q / v["qty"]))
    pnl = proceeds_part - (amount + fee)
    v["qty"] -= q
    v["proceeds"] -= proceeds_part
    v["margin"] -= margin_part
    st["short_realized"] += pnl
    st["short_realized_by"][code] += pnl
    st["ledger"]["fees"] += fee
    st["fees_by"][code] += fee
    st["trades_by"][code] += 1
    st["c"]["short_best"] = max(st["c"]["short_best"], pnl)
    label = "반대매매" if forced else "공매도 상환"
    cash_move(st, margin_part + proceeds_part - amount - fee, label,
              f"{s['name']} {q:,}주 × {price:,}원 되사서 상환 + 수수료 {fee:,}원 (손익 {pnl:+,.0f}원)", code)
    record_trade(st, s, "나(상환)", 1, q)
    st["seq"] += 1
    st["my_trades"].insert(0, {"id": st["seq"], "clock": market_clock(st["ticks"]), "code": code, "name": s["name"],
                               "side": -2, "qty": q, "price": price, "amount": amount, "fee": fee,
                               "pnl": round(pnl), "pnl_pct": round(pnl / proceeds_part * 100, 2) if proceeds_part else 0,
                               "idx": s["hist_total"] - 1})
    del st["my_trades"][100:]
    set_price(s, s["fp"] * (1 + (q / 1000) * 0.003))  # 되사는 주문도 매수세
    return pnl, fee


def check_margin(st):
    """주가가 올라 담보가 모자라면 공매도를 강제로 상환한다"""
    for code, v in st["shorts"].items():
        if not v["qty"]:
            continue
        liability = v["qty"] * st["stocks"][code]["price"]
        if v["proceeds"] + v["margin"] < liability * SHORT_CALL:
            name = st["stocks"][code]["name"]
            pnl, _ = cover_short(st, code, v["qty"], forced=True)
            st["c"]["short_calls"] += 1
            add_news(st, {"type": "sec", "name": name, "code": code, "headline": f"{name} 공매도 담보 부족… 반대매매로 강제 상환", "text": "", "believed": None},
                     toast=f"반대매매. {name} 공매도가 강제 상환됐다 (손익 {pnl:+,.0f}원)")


def charge_borrow(st):
    if st["ticks"] % SALARY_TICKS:
        return
    rate = BORROW_RATE * (0.5 if has_perk(st, 3) else 1.0)
    for code, v in st["shorts"].items():
        if v["qty"]:
            fee = int(v["qty"] * st["stocks"][code]["price"] * rate)
            if fee:
                st["ledger"]["borrow"] += fee
                cash_move(st, -fee, "대차 수수료", f"{st['stocks'][code]['name']} {v['qty']:,}주 빌린 비용 (1분 {rate * 100:.2f}%)", code)


def stock_value(st):
    return sum(st["holdings"][c] * s["price"] for c, s in st["stocks"].items())


def duel_escrow(st):
    d = st.get("duel")
    return d["stake"] if d and d.get("stage") == "fight" else 0


def short_equity(st):
    """공매도 순자산 = 담보(매도 대금 + 증거금) − 지금 사서 갚는 데 드는 돈"""
    return sum(v["proceeds"] + v["margin"] - v["qty"] * st["stocks"][c]["price"] for c, v in st["shorts"].items() if v["qty"])


def short_unrealized(st):
    return sum(v["proceeds"] - v["qty"] * st["stocks"][c]["price"] for c, v in st["shorts"].items() if v["qty"])


def total_assets(st):
    """총자산 = 현금 + 주식 평가금액 + 기업 지분 가치 + 대결 판돈 + 공매도 순자산"""
    return st["cash"] + stock_value(st) + stake_value(st) + duel_escrow(st) + short_equity(st)


def avg_price(st, code):
    """매입단가 = 매입원가(수수료 포함) ÷ 보유 수량"""
    h = st["holdings"][code]
    return st["book"][code] / h if h else 0.0


def level_of(total):
    lv = 1
    for i, (floor, _, _) in enumerate(LEVELS):
        if total >= floor:
            lv = i + 1
    return lv


def level_info(st):
    total = total_assets(st)
    lv = level_of(total)
    floor, name, perk = LEVELS[lv - 1]
    nxt = LEVELS[lv] if lv < len(LEVELS) else None
    return {"level": lv, "name": name, "perk": perk, "floor": floor,
            "next": nxt[0] if nxt else None, "next_name": nxt[1] if nxt else None, "next_perk": nxt[2] if nxt else None,
            "peak": st["level_peak"],
            "table": [{"level": i + 1, "floor": f, "name": n, "perk": p} for i, (f, n, p) in enumerate(LEVELS)]}


def has_perk(st, lv):
    """혜택은 한 번 도달한 최고 레벨 기준으로 유지된다 (인수 후 자산이 잠깐 줄어도 잠기지 않게)"""
    return max(st["level_peak"], level_of(total_assets(st))) >= lv


def tier_of(followers):
    i = max(k for k, t in enumerate(TIERS) if followers >= t[0])
    nxt = TIERS[i + 1] if i + 1 < len(TIERS) else None
    return {"name": TIERS[i][1], "level": i + 1, "floor": TIERS[i][0],
            "next": nxt[0] if nxt else None, "next_name": nxt[1] if nxt else None}


def item_price(st, key):
    p = ITEMS[key]["price"]
    return int(p * 0.9) if has_perk(st, 2) else p


# =================================================================
# 뉴스 / 이벤트
# =================================================================
def add_news(st, item, toast=None, extra=None):
    st["seq"] += 1
    item = {"id": st["seq"], "clock": market_clock(st["ticks"]), **item}
    st["feed"].insert(0, item)
    del st["feed"][50:]
    if toast:
        push_event(st, item["type"], toast, extra)
    return item


def push_event(st, etype, text, extra=None):
    st["seq"] += 1
    st["events"].append({"id": st["seq"], "type": etype, "text": text, **(extra or {})})
    del st["events"][:-25]


# =================================================================
# 실시간 검색어
# =================================================================
def keywords_from(text, company_name):
    words = []
    for raw in re.split(r"[\s,.!?…'\"“”‘’()\[\]/~]+", text):
        w = raw.strip()
        if not w or company_name in w:
            continue
        for j in JOSA:
            if w.endswith(j) and len(w) - len(j) >= 2:
                w = w[: -len(j)]
                break
        if len(w) >= 2 and not w.isdigit() and w not in STOPWORDS:
            words.append(w)
    return words[:3]


def bump_search(st, word, score):
    st["search"][word] = st["search"].get(word, 0.0) + score


def update_search(st):
    for k in list(st["search"]):
        st["search"][k] *= 0.993
        if st["search"][k] < 0.3 and k not in BASE_KEYWORDS:
            del st["search"][k]
    for k in BASE_KEYWORDS:
        st["search"][k] = max(1.0, st["search"].get(k, 5.0) + random.gauss(0, 0.35))
    for s in st["stocks"].values():
        h = s["history"]
        move = abs(h[-1] - h[-2]) / h[-2] if len(h) > 1 else 0
        score = max(0.0, move - 0.003) * 400 + max(0.0, s["vol"] - 1.3) * 0.3
        if score > 0:
            bump_search(st, s["name"], score)
    if st["ticks"] % 20 == 0:
        st["search_prev"] = st.get("search_snap", [])
        st["search_snap"] = [k for k, _ in top_search(st, 10)]


def top_search(st, n):
    return sorted(st["search"].items(), key=lambda kv: -kv[1])[:n]


# =================================================================
# 시장 엔진
# =================================================================
def pick_npc():
    r, acc = random.random(), 0.0
    for name, prob, qty_range in NPC_TYPES:
        acc += prob
        if r <= acc:
            return name, qty_range
    return NPC_TYPES[0][0], NPC_TYPES[0][2]


def append_hist(s):
    s["history"].append(s["price"])
    s["hist_total"] += 1
    del s["history"][:-HISTORY_MAX]


def set_price(s, fp):
    """내부 가격(소수)을 가격제한폭 안으로 묶고, 화면 가격은 호가 단위로 맞춘다"""
    s["fp"] = clamp(fp, s["limit_dn"], s["limit_up"])
    s["price"] = round_price(s["fp"])
    s["price"] = max(s["limit_dn"], min(s["limit_up"], s["price"]))


def set_limits(s):
    up, dn = s["open"] * (1 + PRICE_LIMIT), s["open"] * (1 - PRICE_LIMIT)
    s["limit_up"] = int(up // tick_size(up)) * tick_size(up)
    s["limit_dn"] = int(math.ceil(dn / tick_size(dn))) * tick_size(dn)


def record_trade(st, s, who, side, qty):
    s["trades"].append({"t": st["ticks"], "clock": market_clock(st["ticks"]), "who": who, "side": side, "qty": qty, "price": s["price"]})
    del s["trades"][:-40]
    s["volume"] += qty
    s["turnover"] += qty * s["price"]


def push_rumor(s, change, impact, sentiment, amp=1.0):
    """찌라시/뉴스 효과: 30%는 즉시 갭, 70%는 몇 초에 걸친 매수/매도세, 변동성 상승"""
    old = s["price"]
    set_price(s, s["fp"] * (1 + change * 0.4))          # 40%는 즉시 갭
    append_hist(s)
    s["pressure"] += change * 0.6 * (1 - PRESSURE_DECAY)  # 60%는 이어지는 매수/매도세
    sec = SECTORS[s["sector"]]
    s["vol"] = max(s["vol"], min(8.0, 1 + 7 * impact * abs(sentiment) * amp * sec["vol"] ** 0.5))
    return old


def step(st):
    # 시장 전체 움직임 (모든 종목이 업종 베타만큼 따라간다)
    m = random.gauss(0, 0.0006) + st["market_push"]
    st["market_push"] *= 0.9
    for sec_name in st["sector_push"]:
        st["sector_push"][sec_name] *= SECTOR_DECAY

    for s in st["stocks"].values():
        sec = SECTORS[s["sector"]]
        h = s["history"]
        r5 = (h[-1] - h[-6]) / h[-6] if len(h) > 6 else 0.0
        drift = (s["pressure"] + st["sector_push"][s["sector"]] + sec["beta"] * m
                 + sec["momentum"] * r5 * 0.05                       # 추세 추종
                 - sec["revert"] * (s["fp"] / s["open"] - 1) * 0.002)  # 제자리 복귀
        n = random.randint(1, 4) + (1 if s["vol"] > 2 else 0)
        p_buy = clamp(0.5 + (s["pressure"] + st["sector_push"][s["sector"]]) * 25, 0.08, 0.92)
        fp = s["fp"]
        for _ in range(n):
            who, (lo, hi) = pick_npc()
            qty = int(random.randint(lo, hi) * (s["vol"] ** 0.5))
            side = 1 if random.random() < p_buy else -1
            impact = side * (qty / 1000) * 0.003
            noise = random.gauss(0, 0.0011 * sec["vol"] * s["vol"])
            fp *= 1 + impact + drift / n + noise
            set_price(s, fp)
            fp = s["fp"]
            record_trade(st, s, who, side, qty)
        s["pressure"] *= PRESSURE_DECAY
        if abs(s["pressure"]) < 1e-5:
            s["pressure"] = 0.0
        s["vol"] = 1 + (s["vol"] - 1) * VOL_DECAY
        append_hist(s)
    st["ticks"] += 1
    update_indices(st)
    update_search(st)

    if st["live"] and not st["bankrupt"]:
        check_limits(st)
        process_investigations(st)
        check_surges(st)
        update_followers(st)
        update_posts(st)
        pay_dividends(st)
        if st["ticks"] >= st["next_npc_tick"]:
            npc_rumor(st)
        if mod(st, "preview", False) and not st.get("preview_sent") and st["next_macro_tick"] - st["ticks"] <= 20:
            st["preview_sent"] = True
            push_event(st, "insider", MACRO_EVENTS[st["next_macro"]]["text"], {"title": "국회 소식통"})
        if st["ticks"] >= st["next_macro_tick"]:
            macro_event(st)
        pay_salary(st)
        check_margin(st)
        charge_borrow(st)
        update_duel(st)
        track_rumors(st)
        if st["ticks"] - DAY["start"] >= DAY_TICKS:
            new_day(st)
        check_progress(st)


def advance(st):
    now = time.time()
    n = int((now - st["last_tick"]) / TICK_SEC)
    if n <= 0:
        return
    for _ in range(min(n, 20)):
        step(st)
    st["last_tick"] = now if n > 20 else st["last_tick"] + n * TICK_SEC


# =================================================================
# 지수 / 시장 뉴스
# =================================================================
def update_indices(st):
    caps = [(s["price"] * s["shares"], s["price"] / s["open"]) for s in st["stocks"].values()]
    total_cap = sum(c for c, _ in caps)
    st["idx"]["kospi"] = round(st["idx_base"]["kospi"] * sum(c * r for c, r in caps) / total_cap * (1 + st["kospi_drift"]), 2)
    st["kospi_drift"] += random.gauss(0, 0.00015)
    st["idx"]["fx"] = round(st["idx"]["fx"] + random.gauss(0, 0.25), 1)
    st["idx"]["oil"] = round(max(40, st["idx"]["oil"] + random.gauss(0, 0.04)), 2)


def macro_event(st, idx=None, by_president=False):
    ev = MACRO_EVENTS[idx if idx is not None else (st["next_macro"] if st["next_macro"] is not None else random.randrange(len(MACRO_EVENTS)))]
    st["market_push"] += ev["market"] * 1.6 * (1 - 0.9)
    for sec_name, pct in ev["sectors"].items():
        st["sector_push"][sec_name] += pct * 1.6 * (1 - SECTOR_DECAY)
        for s in st["stocks"].values():
            if s["sector"] == sec_name:
                s["vol"] = max(s["vol"], 1 + abs(pct) * 25)
    for k, d in ev["idx"].items():
        st["idx"][k] = round(st["idx"][k] + d, 2)
    for w in ev["kw"]:
        bump_search(st, w, 22)
    affected = sorted(ev["sectors"].items(), key=lambda kv: -abs(kv[1]))
    summary = ", ".join(f"{k} {'강세' if v > 0 else '약세'}" for k, v in affected[:3])
    headline = f"[대통령 담화] {ev['text']}" if by_president else ev["text"]
    add_news(st, {"type": "macro", "name": "대통령실" if by_president else "시황", "headline": headline, "text": summary, "believed": None},
             toast=headline, extra={"summary": summary})
    if not by_president:
        st["next_macro_tick"] = st["ticks"] + random.randint(*MACRO_TICKS)
        st["next_macro"] = random.randrange(len(MACRO_EVENTS))
        st["preview_sent"] = False


def check_limits(st):
    """상한가/하한가 진입 감지"""
    for code, s in st["stocks"].items():
        state_now = "up" if s["price"] >= s["limit_up"] else "dn" if s["price"] <= s["limit_dn"] else None
        if state_now and s["at_limit"] != state_now:
            word = "상한가" if state_now == "up" else "하한가"
            add_news(st, {"type": "extra" if state_now == "up" else "sec", "name": s["name"], "code": code,
                          "headline": f"{s['name']} {word} 직행", "text": "", "believed": None})
            bump_search(st, f"{s['name']} {word}", 25)
            lr = st["last_rumor"]
            if state_now == "up" and lr and lr["code"] == code and lr["dir"] > 0 and st["ticks"] - lr["tick"] <= 80:
                st["c"]["limit_up_maker"] += 1
        s["at_limit"] = state_now


# =================================================================
# 호외 / 팔로워 / 게시글 / 배당
# =================================================================
def check_surges(st):
    for code, s in st["stocks"].items():
        h = s["history"]
        if len(h) <= SURGE_WINDOW or st["ticks"] < s["surge_cool"]:
            continue
        base = h[-SURGE_WINDOW - 1]
        chg = (h[-1] - base) / base
        if chg >= SURGE_PCT:
            s["surge_cool"] = st["ticks"] + SURGE_COOLDOWN
            pct = round(chg * 100, 1)
            bump_search(st, s["name"], 25)
            add_news(st, {"type": "extra", "name": s["name"], "code": code,
                          "headline": f"{s['name']}, 순식간에 {pct}% 급등", "text": "", "believed": None},
                     toast=f"{s['name']} 급등", extra={"code": code, "pct": pct, "price": s["price"]})


def change_followers(st, delta):
    st["followers"] = max(0, int(st["followers"] + delta))
    st["followers_peak"] = max(st["followers_peak"], st["followers"])


def update_followers(st):
    if st["ticks"] % 6:
        return
    f = st["followers"]
    ret = (total_assets(st) - START_CASH) / START_CASH
    drift = f * 0.004 * (st["credibility"] - 0.5) + f * 0.002 * clamp(ret, -0.5, 1.0)
    drift *= mod(st, "fan", 1.0) if drift > 0 else 1.0
    if st["credibility"] >= 0.6:
        drift += random.randint(0, 2)
    change_followers(st, drift + random.gauss(0, max(0.5, f * 0.001)))
    if st["ticks"] % 12 == 0:
        st["followers_hist"].append(st["followers"])
        del st["followers_hist"][:-80]
        st["assets_hist"].append(total_assets(st))
        del st["assets_hist"][:-80]


def update_posts(st):
    f = st["followers"]
    for p in st["posts"][:12]:
        age = st["ticks"] - p["born"]
        if age > 160:
            continue
        rate = (0.004 if p["believed"] else 0.0012) * (f + 30) * math.exp(-age / 60)
        p["likes"] += sum(random.random() < rate / 3 for _ in range(3))
        if random.random() < rate * 0.25:
            p["comments"] += 1


def follower_power(st):
    f = st["followers"]
    p = mod(st, "fan_power", 1.0)
    return min(0.12 * p, math.log10(f + 1) * 0.025 * p), 1 + min(0.6 * p, f / 80_000 * p)


RUMOR_TRACK_TICKS = 60  # 찌라시 후 30초 동안 주가를 지켜보고 수익을 추정한다


def track_rumors(st):
    for r in st["rumor_log"][:15]:
        if r["price_after"] is not None:
            continue
        p = st["stocks"][r["code"]]["price"]
        r["peak"], r["trough"] = max(r["peak"], p), min(r["trough"], p)
        if st["ticks"] - r["tick"] >= RUMOR_TRACK_TICKS:
            r["price_after"] = p


def new_day(st):
    """장 마감 → 다음 거래일. 시가와 상·하한가가 새로 정해진다"""
    st["day"] += 1
    DAY["start"] = st["ticks"]
    for s in st["stocks"].values():
        s["open"] = s["price"]
        set_limits(s)
        s["volume"] = s["turnover"] = 0
        s["at_limit"] = None
    add_news(st, {"type": "macro", "name": "장 운영", "headline": f"장 마감 · {st['day']}일차 거래 시작", "text": "시가와 상·하한가가 새로 정해졌다", "believed": None},
             toast=f"{st['day']}일차 장 시작", extra={"summary": "시가·상하한가 갱신"})


def update_duel(st):
    d = st["duel"]
    if d is None:
        if st["job"] and st["ticks"] >= st["next_duel_tick"]:
            offer_duel(st)
        return
    if d["stage"] == "offer" and st["ticks"] >= d["expires"]:
        st["duel"] = None
        st["c"]["duel_declines"] += 1
        change_followers(st, -int(st["followers"] * 0.02))
        push_event(st, "duel_end", f"{d['npc']}의 대결 신청을 무시했다", {"title": "대결 무산"})
        st["next_duel_tick"] = st["ticks"] + random.randint(*DUEL_GAP)
    elif d["stage"] == "fight" and st["ticks"] >= d["expires"] and not d.get("judging"):
        resolve_duel(st, None, None)


def offer_duel(st):
    lv = level_of(total_assets(st))
    npc = [n for need, n in DUEL_RIVALS if need <= lv][-1]
    held = [c for c, q in st["holdings"].items() if q > 0]
    code = random.choice(held) if held and random.random() < 0.6 else random.choice(list(st["stocks"]))
    s = st["stocks"][code]
    # 내가 가진 종목이면 주로 악재로 공격해 온다
    direction = -1 if (code in held and random.random() < 0.75) else random.choice([1, -1])
    text = random.choice(SECTOR_RUMORS[s["sector"]]["good" if direction > 0 else "bad"]).format(name=s["name"], n=random.randint(2, 9))
    st["duel"] = {"id": st["seq"] + 1, "stage": "offer", "npc": npc, "strength": DIFFICULTY[lv][4], "level": lv,
                  "code": code, "name": s["name"], "sector": s["sector"], "npc_text": text, "npc_dir": direction,
                  "expires": st["ticks"] + DUEL_OFFER_TICKS, "stake": 0}
    push_event(st, "duel", f"{npc}의 찌라시 대결 신청")


def judge_fallback(d, player_text, a):
    """AI 없이 판정: 내 찌라시 분석 점수 vs 상대 실력"""
    score = 100 * (0.5 * a["plausibility"] + 0.3 * a["impact"] + 0.2 * abs(a["sentiment"])) + random.uniform(-8, 8)
    npc = d["strength"] + random.uniform(-10, 10)
    return {"player": clamp(score, 0, 100), "npc": clamp(npc, 0, 100),
            "reason": "구체성과 업종 적합성, 파급력을 기준으로 판정했다." if score >= npc else "상대 소문이 더 그럴듯하게 퍼졌다."}


def judge_with_ai(d, player_text):
    sec = SECTORS[d["sector"]]
    prompt = f"""
너는 가상 주식시장 게임의 '찌라시 대결' 심판이야. 회사는 가상이다.
[종목] {d['name']} ({d['sector']}) — {sec['desc']}
[상대 {d['npc']}의 찌라시 ({'호재' if d['npc_dir'] > 0 else '악재'})] "{d['npc_text']}"
[플레이어의 반박 찌라시] "{player_text}"

투자자들이 어느 쪽 이야기를 더 믿고 따라갈지 판정해.
- 구체적인 숫자·시점·출처, 업종 적합성, 상대 주장을 정면으로 반박하는지, 파급력을 본다.
- 상대 주장을 반박하지 않고 엉뚱한 얘기를 하면 크게 감점.
- 황당한 얘기는 낮은 점수.
JSON으로만 답해:
{{"player": 0~100 점수, "npc": 0~100 점수, "reason": "판정 이유 한두 문장, 증권가 말투", "headline": "대결 결과를 다룬 뉴스 제목 (30자 이내)"}}
"""
    return ai_json("너는 공정한 찌라시 대결 심판이야. 오직 JSON으로만 답해.", prompt, temperature=0.3)


def resolve_duel(st, player_text, a, ai_judge=None):
    d = st["duel"]
    s = st["stocks"][d["code"]]
    stake = d["stake"]
    if player_text is None:  # 시간 초과
        res = {"player": 0, "npc": d["strength"], "reason": "반박할 시간을 놓쳤다. 상대 소문이 그대로 퍼졌다."}
    elif ai_judge:
        res = {"player": clamp(ai_judge.get("player"), 0, 100), "npc": clamp(ai_judge.get("npc"), 0, 100),
               "reason": str(ai_judge.get("reason", ""))[:200], "headline": str(ai_judge.get("headline", ""))[:60]}
        # 난이도: 레벨이 높을수록 상대가 노련하다
        res["npc"] = clamp(res["npc"] + (d["level"] - 1) * 2.5, 0, 100)
    else:
        res = judge_fallback(d, player_text, a)
    pdir = 0
    if a:
        pdir = 1 if a["sentiment"] > 0.1 else -1 if a["sentiment"] < -0.1 else 0
        if pdir == d["npc_dir"]:  # 반박이 아니라 같은 편을 들면 감점
            res["player"] *= 0.6
            res["reason"] = (res["reason"] + " 다만 상대 주장을 반박하지 않고 같은 방향 얘기를 했다.").strip()
        res["player"] = clamp(res["player"] + follower_power(st)[0] * 40, 0, 100)  # 팔로워가 많으면 여론전에 유리
    win = res["player"] > res["npc"]
    fdelta = 0
    st["ledger"]["duel"] += stake if win else -stake
    if win:
        cash_move(st, stake * 2, "대결 상금", f"{d['npc']}과의 대결 승리 (판돈 {stake:,}원 × 2)", d["code"])
        st["c"]["duel_wins"] += 1
        fdelta = int(st["followers"] * 0.08) + random.randint(30, 80) * d["level"]
        st["credibility"] = clamp(st["credibility"] + 0.05, 0.1, 1.0)
        move = (pdir or -d["npc_dir"]) * 0.06 * SECTORS[s["sector"]]["sens"]
    else:
        st["c"]["duel_losses"] += 1
        fdelta = -int(st["followers"] * 0.05)
        st["credibility"] = clamp(st["credibility"] - 0.03, 0.1, 1.0)
        move = d["npc_dir"] * 0.06 * SECTORS[s["sector"]]["sens"]
    change_followers(st, fdelta * (mod(st, "fan", 1.0) if fdelta > 0 else 1))
    push_rumor(s, move, 0.6, 0.8)
    headline = res.get("headline") or (f"{st['nickname']}, {d['npc']}과의 찌라시 대결 승리" if win else f"{d['npc']}, 찌라시 대결서 {st['nickname']} 제압")
    add_news(st, {"type": "duel", "name": d["name"], "code": d["code"], "headline": headline,
                  "text": player_text or "", "believed": win, "reaction": "대결 승리" if win else "대결 패배"})
    result = {"win": win, "player": round(res["player"]), "npc": round(res["npc"]), "reason": res["reason"],
              "stake": stake, "payout": stake * 2 if win else 0, "followers": fdelta, "npc_name": d["npc"],
              "npc_text": d["npc_text"], "player_text": player_text, "name": d["name"], "judge": "ai" if ai_judge else "basic"}
    st["duel_hist"].insert(0, result)
    del st["duel_hist"][10:]
    st["duel"] = None
    st["next_duel_tick"] = st["ticks"] + random.randint(*DUEL_GAP)
    push_event(st, "duel_end", f"찌라시 대결 {'승리' if win else '패배'}", {"title": "대결 결과", "result": result})
    check_progress(st)
    return result


def pay_salary(st):
    if not st["job"] or st["ticks"] % SALARY_TICKS:
        return
    j = job_of(st)
    pay = j["mods"].get("salary", 0) + int(st["followers"] * j["mods"].get("ad", 0))
    if pay <= 0:
        return
    label = {"office": "월급", "civil": "월급", "student": "알바비", "heir": "용돈", "lawmaker": "세비", "youtuber": "광고 수익"}.get(st["job"], "급여")
    cash_move(st, pay, label, f"{j['name']} {label}" + (f" (팔로워 {st['followers']:,}명)" if j['mods'].get('ad') else ""))
    st["c"]["salary"] += pay
    push_event(st, "ok", f"{label} {pay:,}원 입금", {"title": f"{label} 입금"})


def pay_dividends(st):
    if not st["stakes"] or st["ticks"] % DIVIDEND_TICKS:
        return
    mult = (2 if has_perk(st, 6) else 1) * mod(st, "div", 1.0)
    total = 0
    for code, v in st["stakes"].items():
        total += int(v["shares"] * st["stocks"][code]["price"] * 0.004 * mult)
    names = ", ".join(st["stocks"][c]["name"] for c in st["stakes"])
    cash_move(st, total, "배당", f"인수 기업 배당 ({names})")
    st["c"]["dividends"] += total
    push_event(st, "ok", f"배당금 {total:,}원 입금", {"title": "배당금 입금"})


# =================================================================
# 금감원
# =================================================================
def process_investigations(st):
    for inv in list(st["pending"]):
        if st["ticks"] < inv["due"]:
            continue
        st["pending"].remove(inv)
        s = st["stocks"][inv["code"]]
        short = inv["text"][:18] + ("…" if len(inv["text"]) > 18 else "")
        if st["inventory"]["lawyer"] > 0:
            st["inventory"]["lawyer"] -= 1
            add_news(st, {"type": "sec", "name": s["name"], "headline": "금감원 조사, 변호사가 막아냈다", "text": inv["text"], "believed": None},
                     toast=f"금감원이 '{short}' 유포자를 조사했지만 변호사가 막아냈다")
            continue
        fine = int(total_assets(st) * (0.05 + 0.10 * inv["impact"]) / 1000) * 1000
        if has_perk(st, 4):
            fine = int(fine * 0.7 / 1000) * 1000
        fine = int(fine * mod(st, "fine", 1.0) / 1000) * 1000
        cash_move(st, -fine, "과징금", f"금감원 과징금 · '{short}'", inv["code"])
        st["c"]["fines"] += fine
        st["credibility"] = clamp(st["credibility"] - 0.15, 0.1, 1.0)
        st["c"]["caught"] += 1
        lost = int(st["followers"] * 0.2)
        change_followers(st, -lost)
        s["pressure"] -= inv["direction"] * 0.08 * inv["impact"] * (1 - PRESSURE_DECAY)
        s["vol"] = max(s["vol"], 2.5)
        bump_search(st, "금감원", 20)
        add_news(st, {"type": "sec", "name": s["name"], "headline": f"금감원, 찌라시 유포자 적발… 과징금 {fine:,}원", "text": inv["text"], "believed": None},
                 toast=f"금감원 적발. '{short}' 유포로 과징금 {fine:,}원, 팔로워 {lost:,}명 이탈")
        if mod(st, "lose_job", False):
            old = JOBS[st["job"]]["name"]
            word = "탄핵 소추안 가결" if st["job"] == "president" else "의원직 상실"
            if st["job"] == "president":
                st["c"]["impeached"] += 1
            st["job"] = "jobless"
            st["credibility"] = 0.1
            change_followers(st, -int(st["followers"] * 0.3))
            add_news(st, {"type": "sec", "name": old, "headline": f"{st['nickname']} {old}, {word}… 하루아침에 백수로", "text": "", "believed": None},
                     toast=f"{word}. {old}에서 물러나 백수가 됐다")


# =================================================================
# NPC 찌라시
# =================================================================
def npc_rumor(st):
    code = random.choice(list(st["stocks"]))
    s = st["stocks"][code]
    kind = random.choices(["good", "bad", "weird"], [0.46, 0.42, 0.12])[0]
    pool = WEIRD_RUMORS if kind == "weird" else SECTOR_RUMORS[s["sector"]][kind]
    text = random.choice(pool).format(name=s["name"], n=random.randint(2, 9))
    if kind == "weird":
        r = {"sentiment": random.uniform(0.2, 0.8), "plausibility": random.uniform(0.05, 0.25), "impact": random.uniform(0.5, 1.0)}
    else:
        sign = 1 if kind == "good" else -1
        r = {"sentiment": sign * random.uniform(0.4, 1.0), "plausibility": random.uniform(0.4, 0.85), "impact": random.uniform(0.35, 0.9)}
    change, believed, _ = rumor_effect(r, 0.6, s["sector"])
    push_rumor(s, change, r["impact"], r["sentiment"])
    for w in keywords_from(text, s["name"]):
        bump_search(st, w, 6 + 10 * r["impact"])
    bump_search(st, s["name"], 8)
    author = random.choice(NPC_AUTHORS)
    reaction = ("매수세 유입" if change > 0 else "매도세 출회") if believed else "시장 무반응"
    add_news(st, {"type": "npc", "author": author, "name": s["name"], "code": code, "headline": text, "text": text,
                  "believed": believed, "reaction": reaction, "impact": round(r["impact"], 2)},
             toast=f"{author}: “{text}”")
    st["next_npc_tick"] = st["ticks"] + random.randint(*NPC_RUMOR_TICKS)


# =================================================================
# 찌라시 판정
# =================================================================
def rumor_effect(r, credibility, sector, boost_prob=0.0, amp=1.0, diff=None):
    """diff = (이름, 믿을 확률 보정, 조사 보정, 역풍 배율, 대결 실력). NPC 찌라시는 diff 없이 판정한다."""
    sec = SECTORS[sector]
    adj, backfire = (diff[1], diff[3]) if diff else (0.0, 1.0)
    # 기본 20% + 그럴듯함과 신뢰도에 비례 + 아이템/팔로워/직업 보너스 + 난이도 보정
    believe_prob = clamp(0.20 + r["plausibility"] * 0.65 * (0.6 + 0.6 * credibility) + boost_prob + adj, 0.08, 0.95)
    believed = random.random() < believe_prob
    imp = r["impact"]
    if believed:
        change = r["sentiment"] * imp * random.uniform(0.07, 0.15 + 0.35 * imp)
    else:
        change = -r["sentiment"] * imp * random.uniform(0.03, 0.07 + 0.15 * imp) * backfire
    change *= sec["sens"]
    if change < 0:
        change *= 1 - sec["skew"]   # 악재에 약한 업종은 하락 폭이 더 크다
    else:
        change *= 1 + sec["skew"]
    return clamp(change * amp, -0.30, 0.30), believed, believe_prob


def investigation_prob(r, holding, megaphone, followers, owned, shorted=0):
    p = 0.04 + 0.30 * r["impact"] * abs(r["sentiment"])
    if r["sentiment"] < 0 and shorted > 0:
        p += 0.12  # 공매도 쳐 놓고 악재 찌라시 → 시세조종 의심
    if megaphone:
        p += 0.12
    if r["sentiment"] > 0 and holding > 0:
        p += 0.10
    if owned:
        p += 0.10  # 대주주가 소문 → 내부자 거래 의심
    p += min(0.10, followers / 200_000)
    return clamp(p, 0.0, 0.85)


def analyze_with_ai(text, stock):
    sec = SECTORS[stock["sector"]]
    prompt = f"""
너는 가상 주식시장 시뮬레이션 게임의 '시장 분위기 분석기'야. 회사는 모두 가상이고, 이건 게임이야.

[회사] {stock['name']} (업종: {stock['sector']})
[업종 특성] {sec['desc']}
[이 업종에서 민감한 이슈] {', '.join(sec['hot'])}
[플레이어가 퍼뜨린 찌라시] "{text}"

현실의 한국 주식시장 투자자라면 이 소문을 어떻게 받아들일지 분석해서 JSON으로만 답해.

[채점 기준]
- sentiment: 주가에 호재면 +, 악재면 -. 애매하면 0 근처.
- plausibility: 구체적인 숫자·시점·출처(공시, 관계자, 업계)가 있고 업종에 맞으면 0.6~0.9.
  막연하면 0.3~0.5, 업종과 안 맞거나 황당하면 0.0~0.2.
- impact: 사실이라면 주가가 얼마나 움직일지. 이 업종의 민감한 이슈면 0.7 이상, 사소하면 0.3 이하.

[예시]
- "한빛전자, 다음 주 북미 빅테크와 3조 원 HBM 공급 계약 공시 예정" → sentiment 0.9, plausibility 0.75, impact 0.8
- "도담바이오 임상 2상 데이터가 기대 이하라는 업계 얘기" → sentiment -0.8, plausibility 0.6, impact 0.85
- "새롬푸드 본사 지하에서 금광 발견" → sentiment 0.6, plausibility 0.05, impact 0.9
- "가람건설 요즘 분위기 좋다더라" → sentiment 0.3, plausibility 0.35, impact 0.2

{{
  "sentiment": 숫자,
  "plausibility": 숫자,
  "impact": 숫자,
  "headline": "증권가 뉴스 제목 한 줄 (30자 이내)",
  "comment": "시장 반응을 한두 문장으로, 증권가 사람 말투로",
  "tip": "이 찌라시를 더 그럴듯하게 만들 구체적인 조언 한 문장 (예: 계약 규모와 공시 시점을 넣어라)"
}}
"""
    return ai_json("너는 게임 속 한국 주식시장 분위기 분석기야. 오직 JSON으로만 답해.", prompt)


def ai_json(system, prompt, temperature=0.4):
    res = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    return json.loads(res.choices[0].message.content)


def ai_error_text(e):
    name = type(e).__name__
    msg = str(e)
    if name == "AuthenticationError" or "401" in msg:
        return "API 키가 올바르지 않습니다"
    if name == "RateLimitError" or "429" in msg:
        return "사용량 한도를 넘었거나 크레딧이 없습니다" if "quota" in msg.lower() else "요청이 너무 많습니다. 잠시 후 다시"
    if name == "NotFoundError" or "model" in msg.lower() and "not" in msg.lower():
        return f"모델 '{MODEL}'을 쓸 수 없습니다"
    if "timeout" in name.lower() or "timed out" in msg.lower():
        return "응답 시간 초과"
    if name == "APIConnectionError":
        return "OpenAI 서버에 연결하지 못했습니다"
    return f"{name}: {msg[:60]}"


def ai_try(fn, *args):
    """AI 호출을 감싸서 성공/실패 상태를 기록한다. 실패하면 None."""
    if not client:
        return None
    try:
        out = fn(*args)
        AI_STATUS.update(ok=True, error=None)
        return out
    except Exception as e:
        AI_STATUS.update(ok=False, error=ai_error_text(e))
        print(f"[AI 오류] {AI_STATUS['error']} ({type(e).__name__})")
        return None


def analyze_fallback(text, stock):
    """API 키가 없을 때 쓰는 단순 분석기 (업종 맞춤 단어를 본다)"""
    sec = SECTORS[stock["sector"]]
    good = sum(w in text for w in GOOD_WORDS)
    bad = sum(w in text for w in BAD_WORDS)
    fit = sum(w in text for w in sec["hot"])
    off = sum(w in text for name, other in SECTORS.items() if name != stock["sector"] for w in other["hot"] if w not in sec["hot"])
    sentiment = clamp((good - bad) * 0.5 + random.uniform(-0.15, 0.15), -1, 1)
    specific = bool(re.search(r"\d", text))
    plausibility = clamp(0.32 + 0.18 * specific + min(len(text), 80) / 400 + 0.12 * min(fit, 2) - 0.15 * min(off, 2), 0, 1)
    impact = clamp(0.25 + 0.1 * (good + bad) + 0.18 * min(fit, 2), 0, 1)
    mood = "호재" if sentiment > 0.2 else "악재" if sentiment < -0.2 else "소문"
    short = text.replace(stock["name"], "").strip(" ,")[:14]
    if off and not fit:
        tip = f"{stock['sector']} 업종에 맞는 이슈({', '.join(sec['hot'][:3])})로 바꿔 보세요."
    elif not specific:
        tip = "금액, 날짜, 비율 같은 구체적인 숫자를 넣으면 더 그럴듯해집니다."
    elif not fit:
        tip = f"{stock['sector']} 투자자가 민감한 키워드({', '.join(sec['hot'][:3])})를 넣어 보세요."
    elif abs(sentiment) < 0.3:
        tip = "호재인지 악재인지 분명하게 써야 시장이 움직입니다."
    else:
        tip = "'공시 예정', '관계자에 따르면'처럼 출처를 붙이면 신뢰가 올라갑니다."
    return {"sentiment": sentiment, "plausibility": plausibility, "impact": impact,
            "headline": f"{stock['name']}, '{short}…' {mood}에 술렁",
            "comment": "증권가 메신저에 소문이 돌기 시작했다." + (" 업종과 잘 안 맞는 얘기라 반신반의하는 분위기." if off and not fit else ""),
            "tip": tip}


def analyze_rumor(text, stock):
    raw, source = ai_try(analyze_with_ai, text, stock), "ai"
    if not isinstance(raw, dict) or "sentiment" not in raw:
        raw, source = analyze_fallback(text, stock), "fallback"
    return {"sentiment": clamp(raw.get("sentiment"), -1, 1), "plausibility": clamp(raw.get("plausibility"), 0, 1),
            "impact": clamp(raw.get("impact"), 0, 1), "headline": str(raw.get("headline", ""))[:60] or f"{stock['name']} 소문 확산",
            "comment": str(raw.get("comment", ""))[:200], "tip": str(raw.get("tip", ""))[:120], "source": source}


# =================================================================
# 레벨 / 파산 / 업적
# =================================================================
ACHIEVEMENTS = [
    # (id, 이름, 설명, 분류, 조건)
    ("first_rumor", "첫 찌라시", "찌라시를 처음 뿌렸다", "찌라시", lambda st: st["c"]["rumors"] >= 1),
    ("hit5", "족집게", "찌라시 적중 5회", "찌라시", lambda st: st["c"]["hits"] >= 5),
    ("streak3", "3연속 적중", "찌라시가 3번 연속 먹혔다", "찌라시", lambda st: st["c"]["max_hit_streak"] >= 3),
    ("liar", "양치기 소년", "찌라시가 3번 연속 역풍을 맞았다", "찌라시", lambda st: st["c"]["max_miss_streak"] >= 3),
    ("big", "시장을 흔든 자", "파급력 큰 찌라시를 성공시켰다", "찌라시", lambda st: st["c"]["big_hits"] >= 1),
    ("limit_up", "상한가 메이커", "내 찌라시로 상한가를 만들었다", "찌라시", lambda st: st["c"]["limit_up_maker"] >= 1),
    ("first_win", "첫 익절", "수익을 내고 팔았다", "매매", lambda st: st["c"]["win_sells"] >= 1),
    ("first_loss", "손절의 미학", "손해를 보고 팔았다", "매매", lambda st: st["c"]["loss_sells"] >= 1),
    ("daytrader", "단타왕", "거래 30회", "매매", lambda st: st["c"]["trades"] >= 30),
    ("ret50", "수익률 +50%", "총자산 1,500만 원 돌파", "자산", lambda st: st["level_peak"] >= 2),
    ("eok", "억대 자산가", "총자산 1억 원 돌파", "자산", lambda st: st["assets_peak"] >= 100_000_000),
    ("tycoon", "재벌 총수", "Lv.7 달성", "자산", lambda st: st["level_peak"] >= 7),
    ("chairman", "회장님", "총자산 100억 원, 최고 레벨", "자산", lambda st: st["level_peak"] >= 8),
    ("ma1", "첫 경영권 인수", "기업 하나를 인수했다", "M&A", lambda st: st["c"]["acquisitions"] >= 1),
    ("octopus", "문어발 경영", "기업 3곳을 동시에 거느렸다", "M&A", lambda st: st["c"]["max_stakes"] >= 3),
    ("exit", "엑시트", "인수한 기업을 차익 내고 팔았다", "M&A", lambda st: st["c"]["exit_wins"] >= 1),
    ("fans1k", "리딩방 방장", "팔로워 1천 명", "영향력", lambda st: st["followers_peak"] >= 1_000),
    ("fans10k", "여의도 스타", "팔로워 1만 명", "영향력", lambda st: st["followers_peak"] >= 10_000),
    ("regular", "금감원 단골", "금감원에 2번 이상 적발", "흑역사", lambda st: st["c"]["caught"] >= 2),
    ("duel1", "첫 대결 승리", "찌라시 대결에서 이겼다", "대결", lambda st: st["c"]["duel_wins"] >= 1),
    ("duel5", "여론전의 귀재", "찌라시 대결 5승", "대결", lambda st: st["c"]["duel_wins"] >= 5),
    ("chicken", "쫄보", "대결 신청을 3번 피했다", "대결", lambda st: st["c"]["duel_declines"] >= 3),
    ("short1", "첫 공매도", "빌린 주식을 먼저 팔았다", "매매", lambda st: st["c"]["shorts_opened"] >= 1),
    ("short10m", "하락에 베팅", "공매도 한 번으로 1,000만 원 이상 벌었다", "매매", lambda st: st["c"]["short_best"] >= 10_000_000),
    ("squeeze", "숏 스퀴즈", "공매도가 반대매매를 당했다", "흑역사", lambda st: st["c"]["short_calls"] >= 1),
    ("job1", "첫 직장", "직업을 골랐다", "커리어", lambda st: len(st["jobs_tried"]) >= 1),
    ("hopper", "이직의 달인", "직업 4개를 거쳤다", "커리어", lambda st: len(st["jobs_tried"]) >= 4),
    ("tuber", "구독자 1만", "유튜버로 팔로워 1만 명", "커리어", lambda st: st["job"] == "youtuber" and st["followers"] >= 10_000),
    ("potus", "대통령 당선", "대통령이 됐다", "커리어", lambda st: "president" in st["jobs_tried"]),
    ("impeach", "탄핵", "재임 중 금감원에 적발됐다", "흑역사", lambda st: st["c"]["impeached"] >= 1),
    ("broke", "깡통 계좌", "파산했다", "흑역사", lambda st: st["bankrupt"]),
    ("comeback", "기사회생", "자산 300만 원 아래에서 1,000만 원으로 복귀", "흑역사", lambda st: st["c"]["comeback"]),
]


def check_progress(st):
    total = total_assets(st)
    st["assets_peak"] = max(st["assets_peak"], total)
    st["low_point"] = min(st["low_point"], total)
    if st["low_point"] < 3_000_000 and total >= START_CASH:
        st["c"]["comeback"] = True

    lv = level_of(total)
    if lv > st["level_peak"]:
        new_jobs = [k for k, j in JOBS.items() if st["level_peak"] < j["unlock"] <= lv]
        if new_jobs and st["job"] is not None:
            st["job_offer"] = {"level": lv, "jobs": new_jobs}
        st["level_peak"] = lv
        _, name, perk = LEVELS[lv - 1]
        add_news(st, {"type": "level", "name": "레벨", "headline": f"Lv.{lv} {name} 달성", "text": perk, "believed": None},
                 toast=f"Lv.{lv} {name}", extra={"level": lv, "perk": perk})

    if total < BANKRUPT_LINE and not st["bankrupt"]:
        st["bankrupt"] = True
        add_news(st, {"type": "sec", "name": "계좌", "headline": "반대매매… 계좌가 깡통이 됐다", "text": "", "believed": None},
                 toast="파산", extra={"total": total})

    for aid, name, desc, cat, cond in ACHIEVEMENTS:
        if aid not in st["earned"] and cond(st):
            st["earned"][aid] = market_clock(st["ticks"])
            push_event(st, "badge", name, {"desc": desc})


# =================================================================
# 상태
# =================================================================
def new_state():
    st = {
        "ticks": 0, "last_tick": time.time(), "live": False, "bankrupt": False,
        "cash": START_CASH, "credibility": 0.7,
        "holdings": {c: 0 for c in COMPANIES},
        "book": {c: 0.0 for c in COMPANIES},   # 종목별 매입원가 합계 (매수 수수료 포함)
        "realized": 0.0,                        # 주식 매매 실현손익 (수수료·세금 반영)
        "ledger": {"items": 0, "ma_realized": 0, "duel": 0, "fees": 0, "borrow": 0},
        "realized_by": {c: 0.0 for c in COMPANIES}, "fees_by": {c: 0 for c in COMPANIES}, "trades_by": {c: 0 for c in COMPANIES},
        "cashlog": [], "rumor_log": [], "day": 1,
        "shorts": {c: {"qty": 0, "proceeds": 0, "margin": 0} for c in COMPANIES},
        "short_realized": 0.0, "short_realized_by": {c: 0.0 for c in COMPANIES},
        "my_trades": [], "stakes": {},
        "inventory": {k: 0 for k in ITEMS if ITEMS[k]["kind"] != "instant"},
        "stocks": {}, "feed": [], "events": [], "pending": [], "seq": 0,
        "next_npc_tick": 0, "next_macro_tick": 0,
        "market_push": 0.0, "sector_push": {k: 0.0 for k in SECTORS},
        "idx": {"kospi": 2650.0, "fx": 1385.0, "rate": 3.00, "oil": 78.0}, "idx_base": {"kospi": 2650.0}, "kospi_drift": 0.0,
        "nickname": "익명의 개미", "followers": START_FOLLOWERS, "followers_peak": START_FOLLOWERS,
        "followers_hist": [START_FOLLOWERS], "assets_hist": [START_CASH], "posts": [],
        "level_peak": 1, "assets_peak": START_CASH, "low_point": START_CASH, "earned": {},
        "last_rumor": None,
        "job": None, "jobs_tried": [], "job_offer": None, "policy_cool": 0, "next_macro": None,
        "duel": None, "next_duel_tick": 0, "duel_hist": [],
        "c": {"rumors": 0, "hits": 0, "hit_streak": 0, "miss_streak": 0, "max_hit_streak": 0, "max_miss_streak": 0,
              "big_hits": 0, "limit_up_maker": 0, "trades": 0, "win_sells": 0, "loss_sells": 0,
              "acquisitions": 0, "max_stakes": 0, "exit_wins": 0, "caught": 0, "fines": 0, "dividends": 0, "comeback": False,
              "salary": 0, "impeached": 0, "duel_wins": 0, "duel_losses": 0, "duel_declines": 0,
              "shorts_opened": 0, "short_calls": 0, "short_best": 0},
        "search": {k: random.uniform(3, 9) for k in BASE_KEYWORDS}, "search_prev": [],
    }
    for code, c in COMPANIES.items():
        s = {**c, "open": c["price"], "fp": float(c["price"]), "pressure": 0.0, "vol": 1.0,
             "history": [c["price"]], "hist_total": 1, "trades": [], "volume": 0, "turnover": 0,
             "surge_cool": 0, "at_limit": None}
        set_limits(s)
        st["stocks"][code] = s
    for _ in range(120):
        step(st)
    for s in st["stocks"].values():  # 미리 돌린 구간의 첫 값을 '시가'로
        s["open"] = s["history"][0]
        set_limits(s)
        s["volume"] = s["turnover"] = 0
    st["search_prev"] = st["search_snap"] = [k for k, _ in top_search(st, 10)]
    for s in st["stocks"].values():
        s["trades"] = []  # 미리 돌린 구간의 체결은 지운다 (시각이 어긋나므로)
    st["live"] = True
    st["next_npc_tick"] = st["ticks"] + random.randint(10, 24)
    st["next_macro_tick"] = st["ticks"] + random.randint(30, 60)
    st["next_macro"] = random.randrange(len(MACRO_EVENTS))
    st["next_duel_tick"] = st["ticks"] + random.randint(*DUEL_FIRST)
    DAY["start"] = st["ticks"]
    st["cashlog"] = [{"id": 0, "day": 1, "clock": market_clock(st["ticks"]), "kind": "입금", "desc": "계좌 개설 · 예수금 입금",
                      "code": None, "amount": START_CASH, "balance": START_CASH}]
    st["last_tick"] = time.time()
    return st


state = new_state()


def public_state():
    stocks = {}
    for code, s in state["stocks"].items():
        recent = s["trades"][-30:]
        buy = sum(t["qty"] for t in recent if t["side"] > 0)
        total = sum(t["qty"] for t in recent) or 1
        stocks[code] = {
            "name": s["name"], "sector": s["sector"], "price": s["price"], "open": s["open"],
            "limit_up": s["limit_up"], "limit_dn": s["limit_dn"], "at_limit": s["at_limit"],
            "vol": round(s["vol"], 2), "history": s["history"][-180:], "hist_total": s["hist_total"],
            "trades": list(reversed(s["trades"][-25:])),
            "buy_ratio": round(buy / total, 3), "volume": s["volume"], "turnover": s["turnover"],
            "shares": s["shares"], "cap": s["price"] * s["shares"],
            "takeover_cost": int(s["price"] * s["shares"] * STAKE_RATIO * (1 + mod(state, "premium", TAKEOVER_PREMIUM))),
            "sector_desc": SECTORS[s["sector"]]["desc"],
        }
    unrealized = sum(state["holdings"][c] * state["stocks"][c]["price"] - state["book"][c] for c in state["holdings"] if state["holdings"][c])
    stake_unreal = sum(v["shares"] * state["stocks"][c]["price"] - v["cost"] for c, v in state["stakes"].items())
    total = total_assets(state)
    L = state["ledger"]
    breakdown = {
        "stock_unrealized": round(unrealized),
        "stock_realized": round(state["realized"]),
        "stake_unrealized": round(stake_unreal),
        "ma_realized": round(L["ma_realized"]),
        "income": round(state["c"]["salary"] + state["c"]["dividends"]),
        "duel": round(L["duel"]),
        "items": -round(L["items"]),
        "fines": -round(state["c"]["fines"]),
        "short_unrealized": round(short_unrealized(state)),
        "short_realized": round(state["short_realized"]),
        "borrow": -round(L["borrow"]),
    }
    net = total - START_CASH
    breakdown["other"] = net - sum(breakdown.values())  # 검산용: 항상 0이어야 한다
    prev = state["search_prev"]
    search = [{"word": k, "delta": (prev.index(k) - i) if k in prev else None,
               "code": next((c for c, s in state["stocks"].items() if s["name"] == k), None)}
              for i, (k, _) in enumerate(top_search(state, 10))]
    c = state["c"]
    stakes = [{"code": code, "name": state["stocks"][code]["name"], "shares": v["shares"], "cost": v["cost"],
               "value": v["shares"] * state["stocks"][code]["price"]} for code, v in state["stakes"].items()]
    return {
        "brand": BRAND,
        "clock": market_clock(state["ticks"]),
        "cash": state["cash"],
        "avg_cost": {c: round(avg_price(state, c), 2) for c in state["holdings"]},
        "book": {c: round(v) for c, v in state["book"].items()},
        "realized": round(state["realized"]), "unrealized": round(unrealized),
        "assets": {"cash": state["cash"], "stock": stock_value(state), "stake": stake_value(state), "escrow": duel_escrow(state),
                   "short": round(short_equity(state)), "total": total},
        "shorts": {c: {"qty": v["qty"], "avg": round(v["proceeds"] / v["qty"], 2) if v["qty"] else 0, "proceeds": round(v["proceeds"]),
                       "margin": round(v["margin"]), "liability": v["qty"] * state["stocks"][c]["price"],
                       "unrealized": round(v["proceeds"] - v["qty"] * state["stocks"][c]["price"]) if v["qty"] else 0,
                       "ratio": round((v["proceeds"] + v["margin"]) / (v["qty"] * state["stocks"][c]["price"]), 3) if v["qty"] else None,
                       "call_price": int((v["proceeds"] + v["margin"]) / (v["qty"] * SHORT_CALL)) if v["qty"] else None}
                   for c, v in state["shorts"].items()},
        "short_rules": {"margin": SHORT_MARGIN, "call": SHORT_CALL, "borrow": BORROW_RATE * (0.5 if has_perk(state, 3) else 1.0)},
        "net": net, "breakdown": breakdown, "fees_paid": round(L["fees"]),
        "day": state["day"], "sec_per_tick": MARKET_SEC_PER_TICK,
        "cashlog": state["cashlog"][:300],
        "by_stock": [{"code": c, "name": state["stocks"][c]["name"], "sector": state["stocks"][c]["sector"],
                      "holdings": state["holdings"][c], "book": round(state["book"][c]), "avg": round(avg_price(state, c), 2),
                      "price": state["stocks"][c]["price"], "value": state["holdings"][c] * state["stocks"][c]["price"],
                      "unrealized": round(state["holdings"][c] * state["stocks"][c]["price"] - state["book"][c]),
                      "realized": round(state["realized_by"][c]), "fees": state["fees_by"][c], "trades": state["trades_by"][c],
                      "short_qty": state["shorts"][c]["qty"], "short_unrealized": round(state["shorts"][c]["proceeds"] - state["shorts"][c]["qty"] * state["stocks"][c]["price"]) if state["shorts"][c]["qty"] else 0,
                      "short_realized": round(state["short_realized_by"][c])}
                     for c in COMPANIES if state["trades_by"][c] or state["holdings"][c] or state["shorts"][c]["qty"]],
        "rumor_log": [{**{k: v for k, v in r.items() if k != "tick"},
                       "now": state["stocks"][r["code"]]["price"],
                       "move_pct": round(((r["price_after"] or state["stocks"][r["code"]]["price"]) / r["price_at"] - 1) * 100, 2),
                       "pnl_est": round(((r["price_after"] or state["stocks"][r["code"]]["price"]) - r["price_at"]) * (r["held_at"] - r.get("short_at", 0))),
                       "done": r["price_after"] is not None} for r in state["rumor_log"][:40]],
        "my_trades": state["my_trades"][:60], "credibility": state["credibility"], "holdings": state["holdings"],
        "inventory": state["inventory"], "items": {k: {**v, "price": item_price(state, k)} for k, v in ITEMS.items()},
        "stocks": stocks, "feed": state["feed"], "events": state["events"], "watch": len(state["pending"]),
        "total": total_assets(state), "start_cash": START_CASH, "search": search,
        "idx": state["idx"], "idx_base": {"kospi": state["idx_base"]["kospi"], "fx": 1385.0, "rate": 3.0, "oil": 78.0},
        "level": level_info(state), "stakes": stakes, "stake_value": stake_value(state),
        "bankrupt": state["bankrupt"], "danger": total_assets(state) < 2_000_000,
        "fees": {"fee": 0 if (has_perk(state, 3) or mod(state, "fee_free", False)) else FEE_RATE, "tax": TAX_RATE},
        "job": state["job"],
        "jobs": [{"key": k, "name": j["name"], "icon": j["icon"], "unlock": j["unlock"], "pros": j["pros"], "cons": j["cons"],
                  "unlocked": max(state["level_peak"], level_of(total_assets(state))) >= j["unlock"]} for k, j in JOBS.items()],
        "job_offer": state["job_offer"],
        "duel": ({k: v for k, v in state["duel"].items() if k not in ("strength",)} | {"left": max(0, state["duel"]["expires"] - state["ticks"]) * TICK_SEC})
                if state["duel"] else None,
        "duel_record": {"w": state["c"]["duel_wins"], "l": state["c"]["duel_losses"]},
        "policy": {"on": bool(mod(state, "policy", False)), "cool": max(0, state["policy_cool"] - state["ticks"]) * TICK_SEC,
                   "events": [{"i": i, "text": e["text"]} for i, e in enumerate(MACRO_EVENTS)]},
        "profile": {
            "nickname": state["nickname"], "followers": state["followers"], "followers_hist": state["followers_hist"][-60:],
            "assets_hist": state["assets_hist"][-60:], "tier": tier_of(state["followers"]),
            "rumors_total": c["rumors"], "rumors_hit": c["hits"], "hit_rate": round(c["hits"] / c["rumors"], 3) if c["rumors"] else 0,
            "caught": c["caught"], "fines": c["fines"], "dividends": c["dividends"], "trades": c["trades"], "salary": c["salary"],
            "jobs_tried": state["jobs_tried"],
            "posts": state["posts"][:20],
            "achievements": [{"id": a, "name": n, "desc": d, "cat": cat, "earned": a in state["earned"], "when": state["earned"].get(a)}
                             for a, n, d, cat, _ in ACHIEVEMENTS],
        },
        "ai_on": client is not None, "ai": {**AI_STATUS, "model": MODEL if client else None},
        "difficulty": {"name": difficulty(state)[0], "believe": difficulty(state)[1], "inv": difficulty(state)[2]},
        "tick_ms": int(TICK_SEC * 1000),
    }


# =================================================================
# 라우트
# =================================================================
def guard():
    if state["bankrupt"]:
        return jsonify({"error": "파산한 계좌입니다. 처음부터 다시 시작하세요."}), 400
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    with LOCK:
        advance(state)
        return jsonify(public_state())


@app.route("/api/rumor", methods=["POST"])
def rumor():
    if (g := guard()):
        return g
    data = request.get_json(force=True)
    code = data.get("code")
    text = (data.get("text") or "").strip()
    use = [k for k in (data.get("items") or []) if k in ITEMS and ITEMS[k]["kind"] == "rumor"]
    if code not in COMPANIES or not text:
        return jsonify({"error": "종목과 내용을 확인하세요."}), 400
    if len(text) > 200:
        return jsonify({"error": "200자 이내로 써 주세요."}), 400
    if any(state["inventory"][k] <= 0 for k in use):
        return jsonify({"error": "보유하지 않은 아이템이 있습니다."}), 400

    analysis = analyze_rumor(text, state["stocks"][code])

    with LOCK:
        advance(state)
        use = [k for k in use if state["inventory"][k] > 0]
        for k in use:
            state["inventory"][k] -= 1
        s = state["stocks"][code]
        owned = code in state["stakes"]
        fan_boost, fan_amp = follower_power(state)
        boost = (0.25 if "reporter" in use else 0.0) + fan_boost + (0.25 if owned and has_perk(state, 7) else 0.0) + mod(state, "believe", 0.0)
        amp = (1.5 if "megaphone" in use else 1.0) * fan_amp * mod(state, "amp", 1.0)
        diff = difficulty(state)
        change, believed, prob = rumor_effect(analysis, state["credibility"], s["sector"], boost, amp, diff)
        old = push_rumor(s, change, analysis["impact"], analysis["sentiment"], amp)

        fan_qty = 0
        if believed and state["followers"] >= 20:
            fan_qty = min(5000, int(state["followers"] * random.uniform(0.03, 0.08)))
            if fan_qty:
                record_trade(state, s, "내 팔로워들", 1 if change > 0 else -1, fan_qty)

        c = state["c"]
        c["rumors"] += 1
        f_before = state["followers"]
        if believed:
            c["hits"] += 1
            c["hit_streak"] += 1
            c["miss_streak"] = 0
            if analysis["impact"] >= 0.7:
                c["big_hits"] += 1
            state["credibility"] = clamp(state["credibility"] + 0.03, 0.1, 1.0)
            change_followers(state, (f_before * 0.04 * analysis["impact"] + random.randint(15, 90) * (0.5 + analysis["impact"]) * (0.5 + state["credibility"])) * mod(state, "fan", 1.0))
        else:
            c["miss_streak"] += 1
            c["hit_streak"] = 0
            state["credibility"] = clamp(state["credibility"] - (0.05 + 0.10 * analysis["impact"]), 0.1, 1.0)
            change_followers(state, -f_before * (0.03 + 0.07 * analysis["impact"]) * mod(state, "fan_loss", 1.0))
        c["max_hit_streak"] = max(c["max_hit_streak"], c["hit_streak"])
        c["max_miss_streak"] = max(c["max_miss_streak"], c["miss_streak"])
        f_delta = state["followers"] - f_before
        state["last_rumor"] = {"code": code, "tick": state["ticks"], "dir": 1 if (believed and change > 0) else -1}

        inv_p = clamp(investigation_prob(analysis, state["holdings"][code], "megaphone" in use, state["followers"], owned, state["shorts"][code]["qty"])
                      + mod(state, "inv", 0.0) + diff[2], 0.0, 0.85)
        if random.random() < inv_p:
            state["pending"].append({"due": state["ticks"] + random.randint(*INVESTIGATION_DELAY), "code": code,
                                     "text": text, "impact": analysis["impact"], "direction": 1 if change > 0 else -1})

        for w in keywords_from(text, s["name"]):
            bump_search(state, w, 8 + 14 * analysis["impact"] * (1.5 if believed else 0.6))
        bump_search(state, s["name"], 10)

        headline = analysis["headline"] if believed else f"{s['name']} \"사실무근\" 해명… 소문 역풍"
        item = add_news(state, {"type": "player", "code": code, "name": s["name"], "text": text, "headline": headline,
                                "believed": believed, "reaction": ("매수세 유입" if change > 0 else "매도세 출회") if believed else "역풍",
                                "impact": round(analysis["impact"], 2)})
        state["rumor_log"].insert(0, {"id": item["id"], "day": state["day"], "clock": item["clock"], "tick": state["ticks"], "code": code,
                                      "name": s["name"], "text": text, "believed": believed, "dir": 1 if change > 0 else -1,
                                      "price_at": old, "held_at": state["holdings"][code], "short_at": state["shorts"][code]["qty"], "price_after": None, "peak": old, "trough": old})
        del state["rumor_log"][60:]
        state["posts"].insert(0, {"id": item["id"], "clock": item["clock"], "code": code, "name": s["name"], "text": text,
                                  "believed": believed, "likes": 0, "comments": 0, "born": state["ticks"]})
        del state["posts"][40:]
        check_progress(state)
        result = {**item, "comment": analysis["comment"], "believe_prob": round(prob, 2), "inv_prob": round(inv_p, 2),
                  "sentiment": analysis["sentiment"], "plausibility": analysis["plausibility"], "impact": analysis["impact"],
                  "old": old, "gap": s["price"], "direction": 1 if change > 0.002 else -1 if change < -0.002 else 0,
                  "items_used": use, "fan_qty": fan_qty, "followers_delta": f_delta, "sector": s["sector"],
                  "sens": SECTORS[s["sector"]]["sens"], "source": analysis["source"], "tip": analysis["tip"], "difficulty": diff[0]}
        return jsonify({"result": result, "state": public_state()})


@app.route("/api/trade", methods=["POST"])
def trade():
    if (g := guard()):
        return g
    data = request.get_json(force=True)
    code = data.get("code")
    try:
        qty = int(data.get("qty", 0))
    except (TypeError, ValueError):
        qty = 0
    if code not in COMPANIES or qty == 0:
        return jsonify({"error": "주문을 확인하세요."}), 400

    with LOCK:
        advance(state)
        s = state["stocks"][code]
        price = s["price"]
        fee_rate = 0 if (has_perk(state, 3) or mod(state, "fee_free", False)) else FEE_RATE
        amount = price * abs(qty)
        fee = int(amount * fee_rate) + (int(amount * TAX_RATE) if qty < 0 else 0)
        if qty > 0 and amount + fee > state["cash"]:
            return jsonify({"error": "주문 가능 현금이 부족합니다."}), 400
        if qty < 0 and -qty > state["holdings"][code]:
            return jsonify({"error": "보유 수량이 부족합니다."}), 400
        fail = mod(state, "limit_fill", 0.7)
        if qty > 0 and s["at_limit"] == "up" and random.random() < fail:
            return jsonify({"error": "상한가 매수 잔량이 쌓여 체결되지 않았습니다."}), 400
        if qty < 0 and s["at_limit"] == "dn" and random.random() < fail:
            return jsonify({"error": "하한가 매도 잔량이 쌓여 체결되지 않았습니다."}), 400

        held = state["holdings"][code]
        pnl = pnl_pct = None
        state["ledger"]["fees"] += fee
        state["fees_by"][code] += fee
        state["trades_by"][code] += 1
        if qty > 0:
            cash_move(state, -(amount + fee), "매수", f"{s['name']} {qty:,}주 × {price:,}원 + 수수료 {fee:,}원", code)
            state["book"][code] += amount + fee
        else:
            q = -qty
            cost_part = state["book"][code] * q / held          # 판 만큼의 매입원가
            proceeds = amount - fee                              # 수수료·세금 뺀 실수령액
            pnl = proceeds - cost_part
            pnl_pct = pnl / cost_part * 100 if cost_part else 0
            cash_move(state, proceeds, "매도", f"{s['name']} {q:,}주 × {price:,}원 − 수수료·세금 {fee:,}원 (실현손익 {pnl:+,.0f}원)", code)
            state["book"][code] = 0.0 if q == held else state["book"][code] - cost_part
            state["realized"] += pnl
            state["realized_by"][code] += pnl
            state["c"]["win_sells" if pnl > 0 else "loss_sells"] += 1
        state["holdings"][code] += qty
        state["c"]["trades"] += 1
        record_trade(state, s, "나", 1 if qty > 0 else -1, abs(qty))
        state["seq"] += 1
        state["my_trades"].insert(0, {"id": state["seq"], "clock": market_clock(state["ticks"]), "code": code, "name": s["name"],
                                      "side": 1 if qty > 0 else -1, "qty": abs(qty), "price": price, "amount": amount,
                                      "fee": fee, "pnl": None if pnl is None else round(pnl),
                                      "pnl_pct": None if pnl_pct is None else round(pnl_pct, 2), "idx": s["hist_total"] - 1})
        del state["my_trades"][100:]
        set_price(s, s["fp"] * (1 + (qty / 1000) * 0.003))
        check_progress(state)
        return jsonify({"state": public_state(), "filled": price, "fee": fee})


@app.route("/api/short", methods=["POST"])
def short():
    """qty > 0: 공매도(빌려서 매도), qty < 0: 상환(되사서 갚기)"""
    if (g := guard()):
        return g
    data = request.get_json(force=True)
    code = data.get("code")
    try:
        qty = int(data.get("qty", 0))
    except (TypeError, ValueError):
        qty = 0
    if code not in COMPANIES or qty == 0:
        return jsonify({"error": "주문을 확인하세요."}), 400
    with LOCK:
        advance(state)
        s, v = state["stocks"][code], state["shorts"][code]
        if qty < 0:
            if -qty > v["qty"]:
                return jsonify({"error": f"상환할 공매도 수량이 부족합니다. (공매도 {v['qty']:,}주)"}), 400
            if s["at_limit"] == "up" and random.random() < mod(state, "limit_fill", 0.7):
                return jsonify({"error": "상한가 매수 잔량이 쌓여 상환 주문이 체결되지 않았습니다."}), 400
            pnl, fee = cover_short(state, code, -qty)
            check_progress(state)
            return jsonify({"state": public_state(), "filled": s["price"], "fee": fee, "pnl": round(pnl)})
        if s["at_limit"] == "dn":
            return jsonify({"error": "하한가에서는 공매도할 수 없습니다."}), 400
        price = s["price"]
        amount = price * qty
        fee = 0 if (has_perk(state, 3) or mod(state, "fee_free", False)) else int(amount * FEE_RATE)
        tax = int(amount * TAX_RATE)
        margin = int(amount * SHORT_MARGIN)
        if margin > state["cash"]:
            return jsonify({"error": f"증거금이 부족합니다. (필요 {margin:,}원 = 매도 금액의 {int(SHORT_MARGIN * 100)}%)"}), 400
        proceeds = amount - fee - tax
        v["qty"] += qty
        v["proceeds"] += proceeds
        v["margin"] += margin
        state["ledger"]["fees"] += fee + tax
        state["fees_by"][code] += fee + tax
        state["trades_by"][code] += 1
        state["c"]["shorts_opened"] += 1
        cash_move(state, -margin, "공매도 증거금", f"{s['name']} {qty:,}주 × {price:,}원 공매도 · 매도 대금 {proceeds:,}원은 담보로 보관", code)
        record_trade(state, s, "나(공매도)", -1, qty)
        state["seq"] += 1
        state["my_trades"].insert(0, {"id": state["seq"], "clock": market_clock(state["ticks"]), "code": code, "name": s["name"],
                                      "side": 2, "qty": qty, "price": price, "amount": amount, "fee": fee + tax,
                                      "pnl": None, "pnl_pct": None, "idx": s["hist_total"] - 1})
        del state["my_trades"][100:]
        set_price(s, s["fp"] * (1 - (qty / 1000) * 0.003))  # 공매도 물량은 매도세
        check_progress(state)
        return jsonify({"state": public_state(), "filled": price, "fee": fee + tax})


@app.route("/api/acquire", methods=["POST"])
def acquire():
    if (g := guard()):
        return g
    code = (request.get_json(force=True) or {}).get("code")
    if code not in COMPANIES:
        return jsonify({"error": "없는 종목입니다."}), 400
    with LOCK:
        advance(state)
        if not has_perk(state, 5):
            return jsonify({"error": f"경영권 인수는 Lv.5 {LEVELS[4][1]}부터 가능합니다."}), 400
        if code in state["stakes"]:
            return jsonify({"error": "이미 경영권을 보유한 기업입니다."}), 400
        s = state["stocks"][code]
        shares = int(s["shares"] * STAKE_RATIO)
        cost = int(s["price"] * shares * (1 + mod(state, "premium", TAKEOVER_PREMIUM)))
        if cost > state["cash"]:
            return jsonify({"error": f"인수 자금이 부족합니다. (필요 {cost:,}원)"}), 400
        cash_move(state, -cost, "기업 인수", f"{s['name']} 지분 {shares:,}주 (경영권 프리미엄 포함)", code)
        state["stakes"][code] = {"shares": shares, "cost": cost}
        state["c"]["acquisitions"] += 1
        state["c"]["max_stakes"] = max(state["c"]["max_stakes"], len(state["stakes"]))
        s["pressure"] += 0.08 * (1 - PRESSURE_DECAY)  # 인수 소식은 호재
        s["vol"] = max(s["vol"], 2.0)
        bump_search(state, f"{s['name']} 인수", 30)
        add_news(state, {"type": "ma", "name": s["name"], "code": code,
                         "headline": f"{state['nickname']}, {s['name']} 경영권 인수… 지분 30% 확보", "text": "", "believed": None},
                 toast=f"{s['name']} 경영권 인수", extra={"desc": f"지분 {shares:,}주 · {cost:,}원"})
        check_progress(state)
        return jsonify({"state": public_state()})


@app.route("/api/divest", methods=["POST"])
def divest():
    if (g := guard()):
        return g
    code = (request.get_json(force=True) or {}).get("code")
    with LOCK:
        advance(state)
        if code not in state["stakes"]:
            return jsonify({"error": "경영권을 보유하지 않은 기업입니다."}), 400
        s = state["stocks"][code]
        v = state["stakes"].pop(code)
        proceeds = int(v["shares"] * s["price"] * (1 - mod(state, "block", BLOCK_DISCOUNT)))
        profit = proceeds - v["cost"]
        cash_move(state, proceeds, "지분 매각", f"{s['name']} 지분 {v['shares']:,}주 블록딜 (차익 {profit:+,}원)", code)
        state["ledger"]["ma_realized"] += profit
        if profit > 0:
            state["c"]["exit_wins"] += 1
        s["pressure"] -= 0.06 * (1 - PRESSURE_DECAY)  # 대주주 지분 매각은 악재
        s["vol"] = max(s["vol"], 2.0)
        bump_search(state, f"{s['name']} 매각", 30)
        add_news(state, {"type": "ma", "name": s["name"], "code": code,
                         "headline": f"{state['nickname']}, {s['name']} 지분 블록딜 매각… 차익 {profit:,}원", "text": "", "believed": None},
                 toast=f"{s['name']} 매각 완료", extra={"desc": f"매각 대금 {proceeds:,}원 · 차익 {profit:+,}원"})
        check_progress(state)
        return jsonify({"state": public_state()})


@app.route("/api/job", methods=["POST"])
def choose_job():
    key = (request.get_json(force=True) or {}).get("job")
    with LOCK:
        advance(state)
        if key not in JOBS:
            return jsonify({"error": "없는 직업입니다."}), 400
        if max(state["level_peak"], level_of(total_assets(state))) < JOBS[key]["unlock"]:
            return jsonify({"error": f"Lv.{JOBS[key]['unlock']}부터 선택할 수 있습니다."}), 400
        changed = key != state["job"]
        first = state["job"] is None
        state["job"] = key
        state["job_offer"] = None
        if key not in state["jobs_tried"]:
            state["jobs_tried"].append(key)
        if changed:
            name = JOBS[key]["name"]
            add_news(state, {"type": "level", "name": "커리어", "headline": f"{state['nickname']}, {name}{'로 시작' if first else '(으)로 전직'}",
                             "text": " · ".join(JOBS[key]["pros"]), "believed": None})
            if key == "president":
                bump_search(state, "대통령 취임", 40)
        check_progress(state)
        return jsonify({"state": public_state()})


@app.route("/api/duel/accept", methods=["POST"])
def duel_accept():
    if (g := guard()):
        return g
    ratio = (request.get_json(force=True) or {}).get("ratio", 0.1)
    with LOCK:
        advance(state)
        d = state["duel"]
        if not d or d["stage"] != "offer":
            return jsonify({"error": "진행 중인 대결 신청이 없습니다."}), 400
        if ratio not in (0.1, 0.25, 0.5):
            ratio = 0.1
        stake = int(max(0, state["cash"]) * ratio / 1000) * 1000
        if stake < 10_000:
            return jsonify({"error": "판돈으로 걸 현금이 부족합니다."}), 400
        cash_move(state, -stake, "대결 판돈", f"{d['npc']}과의 대결에 판돈 예치", d["code"])
        d.update(stage="fight", stake=stake, expires=state["ticks"] + DUEL_FIGHT_TICKS)
        return jsonify({"state": public_state()})


@app.route("/api/duel/decline", methods=["POST"])
def duel_decline():
    with LOCK:
        advance(state)
        d = state["duel"]
        if not d:
            return jsonify({"state": public_state()})
        if d["stage"] == "fight":  # 수락 후 포기 = 패배
            resolve_duel(state, None, None)
        else:
            state["duel"] = None
            state["c"]["duel_declines"] += 1
            change_followers(state, -int(state["followers"] * 0.02))
            state["next_duel_tick"] = state["ticks"] + random.randint(*DUEL_GAP)
            check_progress(state)
        return jsonify({"state": public_state()})


@app.route("/api/duel/submit", methods=["POST"])
def duel_submit():
    if (g := guard()):
        return g
    text = ((request.get_json(force=True) or {}).get("text") or "").strip()
    if not text or len(text) > 200:
        return jsonify({"error": "반박 찌라시를 200자 이내로 써 주세요."}), 400
    with LOCK:
        advance(state)
        d = state["duel"]
        if not d or d["stage"] != "fight":
            return jsonify({"error": "진행 중인 대결이 없습니다."}), 400
        d["judging"] = True
        snapshot = dict(d)
    # AI 호출은 잠금 밖에서 (판정 중에도 시장은 움직인다)
    a = analyze_rumor(text, state["stocks"][snapshot["code"]])
    verdict = ai_try(judge_with_ai, snapshot, text)
    with LOCK:
        if not state["duel"] or state["duel"]["id"] != snapshot["id"]:
            return jsonify({"error": "대결이 이미 끝났습니다."}), 400
        result = resolve_duel(state, text, a, verdict if isinstance(verdict, dict) else None)
        return jsonify({"state": public_state(), "result": result})


@app.route("/api/policy", methods=["POST"])
def policy():
    if (g := guard()):
        return g
    idx = (request.get_json(force=True) or {}).get("i")
    with LOCK:
        advance(state)
        if not mod(state, "policy", False):
            return jsonify({"error": "대통령만 정책을 발표할 수 있습니다."}), 400
        if state["ticks"] < state["policy_cool"]:
            return jsonify({"error": "다음 정책 발표까지 기다려야 합니다."}), 400
        if not isinstance(idx, int) or not 0 <= idx < len(MACRO_EVENTS):
            return jsonify({"error": "정책을 골라 주세요."}), 400
        macro_event(state, idx, by_president=True)
        state["policy_cool"] = state["ticks"] + POLICY_COOLDOWN
        return jsonify({"state": public_state()})


@app.route("/api/shop", methods=["POST"])
def shop():
    if (g := guard()):
        return g
    key = (request.get_json(force=True) or {}).get("item")
    if key not in ITEMS:
        return jsonify({"error": "없는 아이템입니다."}), 400
    with LOCK:
        advance(state)
        price = item_price(state, key)
        if state["cash"] < price:
            return jsonify({"error": "현금이 부족합니다."}), 400
        cash_move(state, -price, "아이템", f"암시장 · {ITEMS[key]['name']}")
        state["ledger"]["items"] += price
        if ITEMS[key]["kind"] == "instant":
            state["credibility"] = clamp(state["credibility"] + 0.15, 0.1, 1.0)
        else:
            state["inventory"][key] += 1
        return jsonify({"state": public_state()})


@app.route("/api/profile", methods=["POST"])
def profile():
    name = ((request.get_json(force=True) or {}).get("nickname") or "").strip()
    if not 2 <= len(name) <= 12:
        return jsonify({"error": "닉네임은 2~12자로 정해 주세요."}), 400
    with LOCK:
        state["nickname"] = name
        return jsonify({"state": public_state()})


@app.route("/api/reset", methods=["POST"])
def reset():
    global state
    with LOCK:
        nickname = state["nickname"]
        state = new_state()
        state["nickname"] = nickname
        return jsonify({"state": public_state()})


if __name__ == "__main__":
    if client:
        print(f"{BRAND} · AI 분석 켜짐 (모델 {MODEL}, 키 위치: {KEY_SOURCE})")
    else:
        print(f"{BRAND} · AI 분석 꺼짐 → 기본 분석기 사용 ({AI_STATUS['error']})")
        print("  .env 파일에 OPENAI_API_KEY=sk-... 형태로 키를 넣으면 AI 분석이 켜집니다.")
    app.run(debug=True, threaded=True)

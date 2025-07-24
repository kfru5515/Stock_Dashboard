import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# 시각화 라이브러리
import matplotlib.pyplot as plt
import seaborn as sns
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False

# 머신러닝 라이브러리
try:
    import xgboost as xgb
    import catboost as cb
    import lightgbm as lgb
    from sklearn.preprocessing import RobustScaler
    from sklearn.metrics import mean_squared_error, r2_score
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    print("⚠️ 머신러닝 라이브러리가 설치되지 않음. 룰 베이스 모드로 실행됩니다.")
    print("   설치: pip install xgboost catboost lightgbm scikit-learn")

class EnhancedStockPredictor:
    def __init__(self, start_date='2015-01-01'):
        self.start_date = start_date
        self.end_date = datetime.now().strftime('%Y-%m-%d')
        self.data = {}
        self.patterns = {}
        self.anomalies = {}
        self.models = {}
        self.ml_features = {}
        self.ml_predictions = {}
        self.risk_history = {}
        self.future_risks = {}
        
    def collect_all_data(self):
        """모든 데이터 수집"""
        print("🌍 종합 데이터 수집 중...")
        
        # 기본 시장 데이터
        market_symbols = {
            'kospi': '^KS11',
            'kosdaq': '^KQ11',
            'sp500': '^GSPC',
            'nasdaq': '^IXIC',
            'nikkei': '^N225',
            'hang_seng': '^HSI'
        }
        
        # 경제 지표
        economic_symbols = {
            'vix': '^VIX',
            'treasury_10y': '^TNX',
            'dxy': 'DX-Y.NYB',
            'gold': 'GC=F',
            'oil': 'CL=F',
            'usd_krw': 'KRW=X'
        }
        
        all_symbols = {**market_symbols, **economic_symbols}
        
        for name, symbol in all_symbols.items():
            try:
                print(f"  - {name.upper()} 수집 중...")
                data = yf.download(symbol, start=self.start_date, end=self.end_date, 
                                 progress=False, auto_adjust=False)
                
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.droplevel(1)
                
                if len(data) > 0:
                    self.data[name] = data.fillna(method='ffill')
                    print(f"    ✅ {len(data)}일 데이터 수집")
                else:
                    print(f"    ⚠️ 데이터 없음")
                    
            except Exception as e:
                print(f"    ❌ {name} 수집 실패: {e}")
                
        print(f"✅ 총 {len(self.data)}개 데이터 소스 수집 완료")
        return self.data
    
    def analyze_patterns(self):
        """패턴 분석"""
        print("\n🔍 패턴 분석 중...")
        
        if 'kospi' not in self.data:
            return {}
        
        kospi_data = self.data['kospi']
        kospi_returns = kospi_data['Close'].pct_change()
        
        # 1. 계절성 패턴
        monthly_data = []
        for month in range(1, 13):
            month_data = kospi_returns[kospi_returns.index.month == month]
            if len(month_data) > 0:
                monthly_data.append({
                    'month': month,
                    'mean': month_data.mean(),
                    'positive_ratio': (month_data > 0).mean()
                })
        
        monthly_pattern = pd.DataFrame(monthly_data).set_index('month')
        
        daily_data = []
        for day in range(5):  # 0-4 (월-금)
            day_data = kospi_returns[kospi_returns.index.dayofweek == day]
            if len(day_data) > 0:
                daily_data.append({
                    'day': day,
                    'mean': day_data.mean(),
                    'positive_ratio': (day_data > 0).mean()
                })
        
        daily_pattern = pd.DataFrame(daily_data).set_index('day')
        
        # 2. 기술적 지표
        current_price = kospi_data['Close'].iloc[-1]
        ma20 = kospi_data['Close'].rolling(20).mean().iloc[-1]
        
        # RSI 계산
        delta = kospi_data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # 3. 변동성 분석
        volatility_20d = kospi_returns.rolling(20).std().iloc[-1] * np.sqrt(252) * 100
        
        self.patterns = {
            'monthly': monthly_pattern,
            'daily': daily_pattern,
            'technical': {
                'current_price': current_price,
                'ma20': ma20,
                'rsi': current_rsi,
                'volatility': volatility_20d
            }
        }
        
        print("✅ 패턴 분석 완료")
        return self.patterns
    
    def detect_anomalies(self):
        """이상치 감지"""
        print("\n🚨 이상치 감지 중...")
        
        if 'kospi' not in self.data:
            return {}
        
        kospi_returns = self.data['kospi']['Close'].pct_change().dropna()
        
        # 통계적 이상치 (±3% 이상)
        extreme_moves = kospi_returns[abs(kospi_returns) > 0.03]
        
        anomaly_list = []
        for date, return_val in extreme_moves.items():
            anomaly_type = "급등" if return_val > 0 else "급락"
            anomaly_list.append({
                'date': date.strftime('%Y-%m-%d'),
                'type': anomaly_type,
                'magnitude': f"{return_val*100:+.2f}%"
            })
        
        # 최근 변동성 위험
        recent_vol = kospi_returns.tail(20).std() * np.sqrt(252) * 100
        historical_vol = kospi_returns.std() * np.sqrt(252) * 100
        vol_risk_ratio = recent_vol / historical_vol
        
        self.anomalies = {
            'extreme_moves': anomaly_list[-10:],  # 최근 10개
            'current_vol_risk': {
                'current': recent_vol,
                'historical': historical_vol,
                'ratio': vol_risk_ratio,
                'level': 'high' if vol_risk_ratio > 1.5 else 'moderate' if vol_risk_ratio > 1.2 else 'low'
            }
        }
        
        print(f"✅ 총 {len(extreme_moves)}개 이상치 감지")
        return self.anomalies

    def calculate_economic_risks_detailed(self):
        """상세한 경제 위험도 계산 및 이력 분석"""
        print("\n💰 상세 경제 위험도 분석 중...")
        
        # 과거 위험도 이력 계산
        self._calculate_risk_history()
        
        # 현재 위험도 계산
        current_risks = self._calculate_current_risks()
        
        # 미래 위험도 예측
        self.future_risks = self._predict_future_risks()
        
        return current_risks
    
    def _calculate_risk_history(self):
        """과거 위험도 이력 계산"""
        if not self.data or 'kospi' not in self.data:
            return
        
        # 최근 60일간의 일별 위험도 계산 (더 안정적)
        kospi_data = self.data['kospi']
        end_date = kospi_data.index[-1]
        start_date = end_date - timedelta(days=90)  # 90일치 데이터로 60일 결과 생성
        
        risk_dates = []
        inflation_risks = []
        deflation_risks = []
        stagflation_risks = []
        
        # 최근 60일 동안 일별로 위험도 계산
        for i in range(30, 61):  # 30일부터 60일까지 (안정적인 계산을 위해)
            target_date = end_date - timedelta(days=60-i)
            
            if target_date < start_date:
                continue
            
            # 해당 날짜까지의 데이터 슬라이싱
            temp_data = {}
            for key, value in self.data.items():
                if len(value) > 0:
                    mask = value.index <= target_date
                    if mask.any():
                        temp_data[key] = value[mask].tail(min(252, len(value[mask])))  # 최대 1년치
            
            if temp_data:
                try:
                    risks = self._calculate_risks_for_data(temp_data)
                    risk_dates.append(target_date)
                    inflation_risks.append(risks['inflation']['risk'])
                    deflation_risks.append(risks['deflation']['risk'])
                    stagflation_risks.append(risks['stagflation']['risk'])
                except:
                    continue
        
        if risk_dates:
            self.risk_history = pd.DataFrame({
                'inflation': inflation_risks,
                'deflation': deflation_risks,
                'stagflation': stagflation_risks
            }, index=risk_dates)
        else:
            # 빈 데이터프레임 생성
            self.risk_history = pd.DataFrame()
    
    def _calculate_current_risks(self):
        """현재 위험도 계산"""
        return self._calculate_risks_for_data(self.data)
    
    def _calculate_risks_for_data(self, data):
        """주어진 데이터로 위험도 계산 (민감도 상향 조정 버전)"""
        risks = {}
        
        # 1. 인플레이션 위험
        inflation_score = 0
        inflation_factors = []
        
        # 달러 약세 (기준 완화)
        if 'dxy' in data and len(data['dxy']) > 20:
            dxy_change = data['dxy']['Close'].pct_change(20).iloc[-1]
            if not np.isnan(dxy_change):
                if dxy_change < -0.02:  # 2% 이상 하락 (기존 -5%)
                    inflation_score += 20
                    inflation_factors.append("달러 약세")
                elif dxy_change < -0.01: # 1% 이상 하락
                    inflation_score += 10

        # 원자재 상승 (기준 완화)
        commodities = {'gold': '금', 'oil': '원유'}
        for commodity, name in commodities.items():
            if commodity in data and len(data[commodity]) > 30:
                commodity_change = data[commodity]['Close'].pct_change(30).iloc[-1]
                if not np.isnan(commodity_change):
                    if commodity_change > 0.08:  # 8% 이상 상승 (기존 15%)
                        inflation_score += 15
                        inflation_factors.append(f"{name} 급등")
                    elif commodity_change > 0.04: # 4% 이상 상승
                        inflation_score += 8
        
        # 낮은 금리 환경 (기준 조정)
        if 'treasury_10y' in data and len(data['treasury_10y']) > 0:
            treasury_yield = data['treasury_10y']['Close'].iloc[-1]
            if not np.isnan(treasury_yield):
                if treasury_yield < 3.0:  # 3.0% 미만 (기존 2.5%)
                    inflation_score += 15
                    inflation_factors.append("낮은 기준금리")
                elif treasury_yield < 4.0: # 4.0% 미만
                    inflation_score += 8
        
        inflation_risk = min(inflation_score, 100)
        
        # 2. 디플레이션 위험
        deflation_score = 0
        deflation_factors = []
        
        # 주식시장 침체 (기준 완화)
        if 'kospi' in data and len(data['kospi']) > 60:
            kospi_change_60d = data['kospi']['Close'].pct_change(60).iloc[-1]
            if not np.isnan(kospi_change_60d):
                if kospi_change_60d < -0.10:  # 10% 이상 하락 (기존 -20%)
                    deflation_score += 25
                    deflation_factors.append("주식시장 침체")
                elif kospi_change_60d < -0.05: # 5% 이상 하락
                    deflation_score += 15

        # 글로벌 시장 동반 침체 (기준 완화)
        global_markets = {'sp500': 'S&P500', 'nasdaq': '나스닥'}
        global_down_count = 0
        for market, name in global_markets.items():
            if market in data and len(data[market]) > 40:
                market_change = data[market]['Close'].pct_change(40).iloc[-1]
                if not np.isnan(market_change) and market_change < -0.08:  # 8% 이상 하락 (기존 -12%)
                    global_down_count += 1
        
        if global_down_count >= 2:
            deflation_score += 20
            deflation_factors.append("글로벌 동반 침체")
        
        # 공포지수(VIX) 급등 (기준 완화)
        if 'vix' in data and len(data['vix']) > 0:
            current_vix = data['vix']['Close'].iloc[-1]
            if not np.isnan(current_vix):
                if current_vix > 25:  # VIX 25 이상 (기존 35) - 시장 불안감 고조
                    deflation_score += 20
                    deflation_factors.append("높은 공포지수")
                elif current_vix > 20: # VIX 20 이상 - 시장 불안감 감지
                    deflation_score += 10
        
        deflation_risk = min(deflation_score, 100)
        
        # 3. 스태그플레이션 위험
        stagflation_score = 0
        stagflation_factors = []
        
        # 원자재 급등 + 경기 둔화 조합 (기준 완화)
        commodity_surge = False
        if 'oil' in data and len(data['oil']) > 30:
            change = data['oil']['Close'].pct_change(30).iloc[-1]
            if not np.isnan(change) and change > 0.10:  # 10% 이상 상승 (기존 12%)
                commodity_surge = True
        
        economic_slowdown = False
        if 'kospi' in data and len(data['kospi']) > 60:
            kospi_change = data['kospi']['Close'].pct_change(60).iloc[-1]
            if not np.isnan(kospi_change) and kospi_change < -0.05:  # 5% 이상 하락 (기존 -8%)
                economic_slowdown = True
        
        if commodity_surge and economic_slowdown:
            stagflation_score += 30 # 가중치 상향
            stagflation_factors.append("공급 충격 및 경기 둔화")
        
        # 환율 불안정성 (기준 완화)
        if 'usd_krw' in data and len(data['usd_krw']) > 30:
            krw_change = data['usd_krw']['Close'].pct_change(30).iloc[-1]
            krw_volatility = data['usd_krw']['Close'].pct_change().rolling(20).std().iloc[-1]
            
            if (not np.isnan(krw_change) and not np.isnan(krw_volatility) and 
                krw_change > 0.03 and krw_volatility > 0.006):  # 3% 상승 + 변동성 (기존 4%, 0.008)
                stagflation_score += 20
                stagflation_factors.append("환율 불안정")
        
        stagflation_risk = min(stagflation_score, 100)
        
        # 종합 위험도 (가중평균)
        overall_risk = (inflation_risk * 0.25 + deflation_risk * 0.35 + stagflation_risk * 0.40)
        
        return {
            'inflation': {'risk': inflation_risk, 'factors': inflation_factors},
            'deflation': {'risk': deflation_risk, 'factors': deflation_factors},
            'stagflation': {'risk': stagflation_risk, 'factors': stagflation_factors},
            'overall': overall_risk
        }
    
    def _predict_future_risks(self):
        """미래 1주일 위험도 예측 (개선된 버전)"""
        if not hasattr(self, 'risk_history') or self.risk_history.empty:
            # 위험도 이력이 없으면 현재 수준에서 소폭 변동만 예측
            current_risks = self._calculate_current_risks()
            future_risks = {}
            
            for risk_type in ['inflation', 'deflation', 'stagflation']:
                current_level = current_risks[risk_type]['risk']
                
                # 매우 보수적인 예측 (±5% 범위)
                random_change = np.random.normal(0, 2)  # 평균 0, 표준편차 2
                predicted_level = max(0, min(100, current_level + random_change))
                
                future_risks[risk_type] = {
                    'predicted': predicted_level,
                    'current': current_level,
                    'change': predicted_level - current_level,
                    'trend': 'stable',
                    'confidence_interval': (max(0, predicted_level - 5), min(100, predicted_level + 5)),
                    'volatility': 5.0
                }
            
            return future_risks
        
        current_risks = self._calculate_current_risks()
        future_risks = {}
        
        # 각 위험 유형별로 트렌드 분석
        for risk_type in ['inflation', 'deflation', 'stagflation']:
            current_level = current_risks[risk_type]['risk']
            
            # 최근 데이터로 트렌드 계산
            if len(self.risk_history) >= 10:
                recent_values = self.risk_history[risk_type].tail(10)
                
                # 선형 회귀로 트렌드 계산
                x = np.arange(len(recent_values))
                y = recent_values.values
                
                # NaN 값 제거
                valid_mask = ~np.isnan(y)
                if np.sum(valid_mask) >= 3:
                    x_valid = x[valid_mask]
                    y_valid = y[valid_mask]
                    
                    slope, intercept = np.polyfit(x_valid, y_valid, 1)
                    trend_per_day = slope  # 일별 변화율
                else:
                    trend_per_day = 0
                
                # 변동성 계산
                volatility = np.nanstd(recent_values) if len(recent_values) > 1 else 5.0
            else:
                trend_per_day = 0
                volatility = 5.0
            
            # 1주일 후 예측 (7일)
            trend_effect = trend_per_day * 7
            
            # 평균 회귀 효과 (극값에서 중앙값으로 되돌아가려는 경향)
            target_mean = 30  # 일반적인 중성 위험도
            mean_reversion = (target_mean - current_level) * 0.05  # 5% 회귀
            
            # 예측값 계산
            predicted_level = current_level + trend_effect + mean_reversion
            
            # 현실적 범위 제한 (급격한 변화 방지)
            max_weekly_change = 15  # 주간 최대 15% 변화
            predicted_level = max(current_level - max_weekly_change, 
                                min(current_level + max_weekly_change, predicted_level))
            predicted_level = max(0, min(100, predicted_level))
            
            # 신뢰구간 계산
            uncertainty = min(volatility * 1.5, 10)  # 최대 10% 불확실성
            lower_bound = max(0, predicted_level - uncertainty)
            upper_bound = min(100, predicted_level + uncertainty)
            
            # 트렌드 분류
            total_change = predicted_level - current_level
            if total_change > 3:
                trend = 'increasing'
            elif total_change < -3:
                trend = 'decreasing'
            else:
                trend = 'stable'
            
            future_risks[risk_type] = {
                'predicted': predicted_level,
                'current': current_level,
                'change': total_change,
                'trend': trend,
                'confidence_interval': (lower_bound, upper_bound),
                'volatility': volatility
            }
        
        return future_risks
    
    def predict_weekly_enhanced(self):
        """향상된 1주일 예측"""
        print("\n🔮 향상된 1주일 예측 생성 중...")
        
        if 'kospi' not in self.data or 'kosdaq' not in self.data:
            return []
        
        current_kospi = self.data['kospi']['Close'].iloc[-1]
        current_kosdaq = self.data['kosdaq']['Close'].iloc[-1]
        
        # 기본 통계
        kospi_returns = self.data['kospi']['Close'].pct_change()
        kosdaq_returns = self.data['kosdaq']['Close'].pct_change()
        
        kospi_vol = kospi_returns.tail(20).std()
        kosdaq_vol = kosdaq_returns.tail(20).std()
        
        predictions = []
        current_risks = self._calculate_current_risks()
        
        for day in range(1, 8):
            target_date = datetime.now() + timedelta(days=day)
            weekday = target_date.weekday()
            weekday_name = ['월', '화', '수', '목', '금', '토', '일'][weekday]
            
            if weekday >= 5:  # 주말
                predictions.append({
                    'date': target_date.strftime('%Y-%m-%d'),
                    'weekday': weekday_name,
                    'status': '휴장',
                    'kospi': current_kospi,
                    'kosdaq': current_kosdaq,
                    'kospi_change': 0,
                    'kosdaq_change': 0,
                    'risk_factors': [],
                    'economic_risks': current_risks
                })
            else:
                # 예측 요소들
                factors = []
                risk_factors = []
                kospi_adjustment = 0
                kosdaq_adjustment = 0
                
                # 1. 경제 위험도 영향
                inflation_risk = current_risks['inflation']['risk']
                deflation_risk = current_risks['deflation']['risk']
                stagflation_risk = current_risks['stagflation']['risk']
                
                # 인플레이션 위험이 높으면 주식에 부정적
                if inflation_risk > 60:
                    kospi_adjustment -= 0.008
                    kosdaq_adjustment -= 0.012
                    risk_factors.append(f"인플레이션위험({inflation_risk:.0f}%)")
                
                # 디플레이션 위험이 높으면 주식에 매우 부정적
                if deflation_risk > 50:
                    kospi_adjustment -= 0.015
                    kosdaq_adjustment -= 0.020
                    risk_factors.append(f"디플레이션위험({deflation_risk:.0f}%)")
                
                # 스태그플레이션 위험이 높으면 가장 부정적
                if stagflation_risk > 40:
                    kospi_adjustment -= 0.012
                    kosdaq_adjustment -= 0.018
                    risk_factors.append(f"스태그플레이션위험({stagflation_risk:.0f}%)")
                
                # 2. 계절성 효과
                if 'monthly' in self.patterns:
                    month = target_date.month
                    if month in self.patterns['monthly'].index:
                        monthly_effect = self.patterns['monthly'].loc[month, 'mean']
                        monthly_prob = self.patterns['monthly'].loc[month, 'positive_ratio']
                        
                        seasonal_weight = abs(monthly_prob - 0.5) * 2
                        kospi_adjustment += monthly_effect * seasonal_weight * 0.3
                        kosdaq_adjustment += monthly_effect * seasonal_weight * 0.35
                        
                        factors.append(f"{month}월계절성({monthly_prob*100:.0f}%)")
                
                # 3. 요일 효과
                if 'daily' in self.patterns and weekday < 5:
                    if weekday in self.patterns['daily'].index:
                        daily_effect = self.patterns['daily'].loc[weekday, 'mean']
                        daily_prob = self.patterns['daily'].loc[weekday, 'positive_ratio']
                        
                        daily_weight = abs(daily_prob - 0.5) * 2
                        kospi_adjustment += daily_effect * daily_weight * 0.2
                        kosdaq_adjustment += daily_effect * daily_weight * 0.25
                        
                        factors.append(f"{weekday_name}요일효과({daily_prob*100:.0f}%)")
                
                # 4. 글로벌 영향
                if 'sp500' in self.data and len(self.data['sp500']) > 0:
                    sp500_change = self.data['sp500']['Close'].pct_change().iloc[-1]
                    if not np.isnan(sp500_change):
                        global_effect = sp500_change * 0.6 * 0.4
                        kospi_adjustment += global_effect
                        kosdaq_adjustment += global_effect * 1.2
                        factors.append(f"S&P500영향({sp500_change*100:+.1f}%)")
                
                # 노이즈 추가 (현실적 변동)
                np.random.seed(day)  # 재현 가능한 예측을 위해
                kospi_noise = np.random.normal(0, kospi_vol) * 0.3
                kosdaq_noise = np.random.normal(0, kosdaq_vol) * 0.3
                
                kospi_total_change = kospi_adjustment + kospi_noise
                kosdaq_total_change = kosdaq_adjustment + kosdaq_noise
                
                # 현실적 범위 제한
                kospi_total_change = np.clip(kospi_total_change, -0.04, 0.04)
                kosdaq_total_change = np.clip(kosdaq_total_change, -0.05, 0.05)
                
                # 예측가 계산
                kospi_pred = current_kospi * (1 + kospi_total_change)
                kosdaq_pred = current_kosdaq * (1 + kosdaq_total_change)
                
                # 당일 경제 위험도 업데이트 (점진적 변화)
                day_risks = current_risks.copy()
                if self.future_risks:
                    for risk_type in ['inflation', 'deflation', 'stagflation']:
                        if risk_type in self.future_risks:
                            change_per_day = self.future_risks[risk_type]['change'] / 7
                            day_risks[risk_type]['risk'] += change_per_day * day
                
                predictions.append({
                    'date': target_date.strftime('%Y-%m-%d'),
                    'weekday': weekday_name,
                    'status': '개장',
                    'kospi': round(kospi_pred, 2),
                    'kosdaq': round(kosdaq_pred, 2),
                    'kospi_change': round(kospi_total_change * 100, 2),
                    'kosdaq_change': round(kosdaq_total_change * 100, 2),
                    'factors': factors,
                    'risk_factors': risk_factors,
                    'economic_risks': day_risks
                })
                
                # 다음날 예측을 위한 기준가 업데이트
                current_kospi = kospi_pred
                current_kosdaq = kosdaq_pred
        
        print("✅ 향상된 1주일 예측 완료")
        return predictions
    
    def create_comprehensive_visualizations(self, predictions, current_risks):
        """종합 시각화 생성"""
        print("\n📊 종합 그래프 생성 중...")
        
        # 한글 폰트 설정 (시스템에 따라 조정 필요)
        try:
            plt.rcParams['font.family'] = 'Malgun Gothic'
        except:
            plt.rcParams['font.family'] = 'DejaVu Sans'
        
        fig = plt.figure(figsize=(20, 16))
        
        # 1. 주가 예측 그래프 (상단 좌측)
        ax1 = plt.subplot(3, 3, (1, 2))
        
        # 과거 30일 + 예측 7일
        kospi_historical = self.data['kospi']['Close'].tail(30)
        kosdaq_historical = self.data['kosdaq']['Close'].tail(30)
        
        # 예측 데이터 준비
        trading_predictions = [p for p in predictions if p['status'] == '개장']
        pred_dates = [datetime.strptime(p['date'], '%Y-%m-%d') for p in trading_predictions]
        kospi_pred = [p['kospi'] for p in trading_predictions]
        kosdaq_pred = [p['kosdaq'] for p in trading_predictions]
        
        # 연결점 추가
        last_date = kospi_historical.index[-1]
        last_kospi = kospi_historical.iloc[-1]
        last_kosdaq = kosdaq_historical.iloc[-1]
        
        # 과거 데이터 플롯
        ax1.plot(kospi_historical.index, kospi_historical.values, 'b-', linewidth=2, label='KOSPI (실제)', alpha=0.8)
        ax1.plot(kosdaq_historical.index, kosdaq_historical.values, 'r-', linewidth=2, label='KOSDAQ (실제)', alpha=0.8)
        
        # 예측 데이터 플롯
        if pred_dates and kospi_pred:
            # 연결선
            ax1.plot([last_date, pred_dates[0]], [last_kospi, kospi_pred[0]], 'b--', alpha=0.5)
            ax1.plot([last_date, pred_dates[0]], [last_kosdaq, kosdaq_pred[0]], 'r--', alpha=0.5)
            
            # 예측선
            ax1.plot(pred_dates, kospi_pred, 'b--', linewidth=2, label='KOSPI (예측)', alpha=0.7)
            ax1.plot(pred_dates, kosdaq_pred, 'r--', linewidth=2, label='KOSDAQ (예측)', alpha=0.7)
            
            # 예측 구간 음영
            ax1.fill_between(pred_dates, 
                           [k * 0.98 for k in kospi_pred], 
                           [k * 1.02 for k in kospi_pred], 
                           color='blue', alpha=0.2)
            ax1.fill_between(pred_dates, 
                           [k * 0.98 for k in kosdaq_pred], 
                           [k * 1.02 for k in kosdaq_pred], 
                           color='red', alpha=0.2)
        
        ax1.set_title('KOSPI/KOSDAQ 주가 예측 (1주일)', fontsize=14, fontweight='bold')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # 2. 경제 위험도 현황 (상단 우측)
        ax2 = plt.subplot(3, 3, 3)
        
        risk_types = ['인플레이션', '디플레이션', '스태그플레이션']
        risk_values = [current_risks['inflation']['risk'], 
                      current_risks['deflation']['risk'], 
                      current_risks['stagflation']['risk']]
        colors = ['#ff6b6b', '#4ecdc4', '#45b7d1']
        
        bars = ax2.bar(risk_types, risk_values, color=colors, alpha=0.8)
        ax2.set_ylim(0, 100)
        ax2.set_ylabel('위험도 (%)')
        ax2.set_title('현재 경제 위험도', fontsize=14, fontweight='bold')
        
        # 위험도별 색상 구분선
        ax2.axhline(y=30, color='green', linestyle='--', alpha=0.5, label='안전')
        ax2.axhline(y=60, color='orange', linestyle='--', alpha=0.5, label='주의')
        ax2.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='위험')
        
        # 수치 표시
        for bar, value in zip(bars, risk_values):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 1,
                    f'{value:.1f}%', ha='center', va='bottom', fontweight='bold')
        
        ax2.tick_params(axis='x', rotation=45)
        
        # 3. 위험도 변화 추이 (중단 좌측)
        ax3 = plt.subplot(3, 3, (4, 5))
        
        if hasattr(self, 'risk_history') and not self.risk_history.empty:
            recent_history = self.risk_history.tail(30)  # 최근 30일
            
            if len(recent_history) > 0:
                ax3.plot(recent_history.index, recent_history['inflation'], 'r-', linewidth=2, label='인플레이션', alpha=0.8)
                ax3.plot(recent_history.index, recent_history['deflation'], 'b-', linewidth=2, label='디플레이션', alpha=0.8)
                ax3.plot(recent_history.index, recent_history['stagflation'], 'g-', linewidth=2, label='스태그플레이션', alpha=0.8)
                
                # 미래 예측 추가
                if self.future_risks:
                    future_date = datetime.now() + timedelta(days=7)
                    current_date = recent_history.index[-1]
                    
                    for risk_type, color in zip(['inflation', 'deflation', 'stagflation'], ['r', 'b', 'g']):
                        if risk_type in self.future_risks:
                            current_val = recent_history[risk_type].iloc[-1]
                            future_val = self.future_risks[risk_type]['predicted']
                            
                            # 예측선
                            ax3.plot([current_date, future_date], [current_val, future_val], 
                                    color=color, linestyle='--', linewidth=2, alpha=0.7)
                            
                            # 신뢰구간
                            lower, upper = self.future_risks[risk_type]['confidence_interval']
                            ax3.fill_between([current_date, future_date], 
                                           [current_val, lower], [current_val, upper], 
                                           color=color, alpha=0.2)
            
            ax3.set_title('경제 위험도 변화 추이 (30일 + 1주일 예측)', fontsize=14, fontweight='bold')
            ax3.legend()
            ax3.grid(True, alpha=0.3)
            ax3.set_ylabel('위험도 (%)')
            ax3.set_ylim(0, 100)  # Y축 범위 고정
            ax3.tick_params(axis='x', rotation=45)
        else:
            # 위험도 이력이 없는 경우 현재 값만 표시
            current_date = datetime.now()
            risk_types = ['인플레이션', '디플레이션', '스태그플레이션']
            risk_values = [current_risks['inflation']['risk'], 
                          current_risks['deflation']['risk'], 
                          current_risks['stagflation']['risk']]
            
            x_pos = [0, 1, 2]
            bars = ax3.bar(x_pos, risk_values, color=['red', 'blue', 'green'], alpha=0.7)
            ax3.set_xticks(x_pos)
            ax3.set_xticklabels(risk_types)
            ax3.set_ylabel('위험도 (%)')
            ax3.set_title('현재 경제 위험도', fontsize=14, fontweight='bold')
            ax3.set_ylim(0, 100)
            
            # 수치 표시
            for bar, value in zip(bars, risk_values):
                height = bar.get_height()
                ax3.text(
                    bar.get_x() + bar.get_width()/2., height + 1,
                    f'{value:.1f}%', 
                    ha='center', va='bottom', fontsize=14, fontweight='bold'
                )
            ax3.legend()
            ax3.grid(True, alpha=0.3)
            ax3.set_ylabel('위험도 (%)')
            ax3.tick_params(axis='x', rotation=45)
        
        # 4. 주요 경제지표 현황 (중단 우측)
        ax4 = plt.subplot(3, 3, 6)
        
        indicators = []
        values = []
        changes = []
        
        # VIX
        if 'vix' in self.data:
            current_vix = self.data['vix']['Close'].iloc[-1]
            vix_change = self.data['vix']['Close'].pct_change(5).iloc[-1] * 100
            indicators.append('VIX')
            values.append(current_vix)
            changes.append(vix_change)
        
        # USD/KRW
        if 'usd_krw' in self.data:
            current_krw = self.data['usd_krw']['Close'].iloc[-1]
            krw_change = self.data['usd_krw']['Close'].pct_change(5).iloc[-1] * 100
            indicators.append('USD/KRW')
            values.append(current_krw)
            changes.append(krw_change)
        
        # 10년 국채
        if 'treasury_10y' in self.data:
            current_treasury = self.data['treasury_10y']['Close'].iloc[-1]
            treasury_change = self.data['treasury_10y']['Close'].pct_change(5).iloc[-1] * 100
            indicators.append('10Y Treasury')
            values.append(current_treasury)
            changes.append(treasury_change)
        
        # Gold
        if 'gold' in self.data:
            current_gold = self.data['gold']['Close'].iloc[-1]
            gold_change = self.data['gold']['Close'].pct_change(5).iloc[-1] * 100
            indicators.append('Gold')
            values.append(current_gold)
            changes.append(gold_change)
        
        if indicators:
            colors = ['red' if c < 0 else 'blue' for c in changes]
            bars = ax4.barh(indicators, changes, color=colors, alpha=0.7)
            ax4.set_xlabel('5일 변화율 (%)')
            ax4.set_title('주요 경제지표 변화', fontsize=14, fontweight='bold')
            ax4.axvline(x=0, color='black', linestyle='-', alpha=0.3)
            
            # 수치 표시
            for i, (bar, value, change) in enumerate(zip(bars, values, changes)):
                width = bar.get_width()
                ax4.text(width + (0.1 if width > 0 else -0.1), bar.get_y() + bar.get_height()/2,
                        f'{change:+.1f}%', ha='left' if width > 0 else 'right', va='center')
        
        # 5. 섹터별 영향 예측 (하단 좌측)
        ax5 = plt.subplot(3, 3, 7)
        
        # 위험도에 따른 섹터별 영향 시뮬레이션
        sectors = ['금융', '기술', '소재', '에너지', '소비재']
        
        # 각 위험 시나리오별 섹터 영향도 계산
        inflation_impact = [-15, -10, +5, +20, -5]  # 인플레이션시 섹터별 영향
        deflation_impact = [-25, -15, -20, -30, -10]  # 디플레이션시 섹터별 영향
        stagflation_impact = [-20, -12, -8, +10, -15]  # 스태그플레이션시 섹터별 영향
        
        # 가중평균으로 종합 영향도 계산
        inflation_weight = current_risks['inflation']['risk'] / 100
        deflation_weight = current_risks['deflation']['risk'] / 100
        stagflation_weight = current_risks['stagflation']['risk'] / 100
        
        total_impact = []
        for i in range(len(sectors)):
            impact = (inflation_impact[i] * inflation_weight + 
                     deflation_impact[i] * deflation_weight + 
                     stagflation_impact[i] * stagflation_weight) / 3
            total_impact.append(impact)
        
        colors = ['red' if x < -10 else 'orange' if x < 0 else 'lightgreen' if x < 10 else 'green' for x in total_impact]
        bars = ax5.bar(sectors, total_impact, color=colors, alpha=0.8)
        
        ax5.set_title('섹터별 1주일 영향 예측', fontsize=14, fontweight='bold')
        ax5.set_ylabel('예상 영향도 (%)')
        ax5.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax5.tick_params(axis='x', rotation=45)
        
        # 수치 표시
        for bar, value in zip(bars, total_impact):
            height = bar.get_height()
            ax5.text(bar.get_x() + bar.get_width()/2., height + (0.5 if height > 0 else -1),
                    f'{value:+.1f}%', ha='center', va='bottom' if height > 0 else 'top', fontweight='bold')
        
        # 6. 변동성 분석 (하단 중앙)
        ax6 = plt.subplot(3, 3, 8)
        
        if 'kospi' in self.data:
            kospi_returns = self.data['kospi']['Close'].pct_change()
            rolling_vol = kospi_returns.rolling(20).std() * np.sqrt(252) * 100
            recent_vol = rolling_vol.tail(60)
            
            ax6.plot(recent_vol.index, recent_vol.values, 'purple', linewidth=2, alpha=0.8)
            ax6.fill_between(recent_vol.index, recent_vol.values, alpha=0.3, color='purple')
            
            # 평균선
            avg_vol = recent_vol.mean()
            ax6.axhline(y=avg_vol, color='red', linestyle='--', alpha=0.7, label=f'평균: {avg_vol:.1f}%')
            
            # 현재값
            current_vol = recent_vol.iloc[-1]
            ax6.axhline(y=current_vol, color='blue', linestyle='-', alpha=0.7, label=f'현재: {current_vol:.1f}%')
            
            ax6.set_title('KOSPI 변동성 추이 (60일)', fontsize=14, fontweight='bold')
            ax6.set_ylabel('연환산 변동성 (%)')
            ax6.legend()
            ax6.grid(True, alpha=0.3)
            ax6.tick_params(axis='x', rotation=45)
        
        # 7. 위험 요인 분석 (하단 우측) - 개선된 버전
        ax7 = plt.subplot(3, 3, 9)
        
        # 모든 위험 요인 수집 및 가중치 부여
        risk_factor_weights = {}
        
        # 인플레이션 요인 (가중치: 인플레이션 위험도 / 100)
        inflation_weight = current_risks['inflation']['risk'] / 100
        for factor in current_risks['inflation']['factors']:
            risk_factor_weights[factor] = risk_factor_weights.get(factor, 0) + inflation_weight
        
        # 디플레이션 요인 (가중치: 디플레이션 위험도 / 100)
        deflation_weight = current_risks['deflation']['risk'] / 100
        for factor in current_risks['deflation']['factors']:
            risk_factor_weights[factor] = risk_factor_weights.get(factor, 0) + deflation_weight
        
        # 스태그플레이션 요인 (가중치: 스태그플레이션 위험도 / 100)
        stagflation_weight = current_risks['stagflation']['risk'] / 100
        for factor in current_risks['stagflation']['factors']:
            risk_factor_weights[factor] = risk_factor_weights.get(factor, 0) + stagflation_weight
        
        if risk_factor_weights:
            # 가중치 기준으로 정렬하고 상위 6개 선택
            sorted_factors = sorted(risk_factor_weights.items(), key=lambda x: x[1], reverse=True)
            top_factors = dict(sorted_factors[:6])
            
            if len(top_factors) > 0:
                factor_names = list(top_factors.keys())
                factor_values = list(top_factors.values())
                
                # 색상 팔레트 설정
                colors = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff9ff3'][:len(factor_names)]
                
                # 파이 차트 생성
                wedges, texts, autotexts = ax7.pie(factor_values, 
                                                  labels=factor_names, 
                                                  autopct='%1.1f%%', 
                                                  colors=colors, 
                                                  startangle=90,
                                                  explode=[0.05] * len(factor_names))  # 약간 분리된 효과
                
                ax7.set_title('주요 위험 요인 분포\n(위험도 가중치 반영)', fontsize=12, fontweight='bold')
                
                # 텍스트 스타일 개선
                for text in texts:
                    text.set_fontsize(8)
                    text.set_fontweight('bold')
                
                for autotext in autotexts:
                    autotext.set_fontsize(8)
                    autotext.set_color('white')
                    autotext.set_fontweight('bold')
            else:
                ax7.text(0.5, 0.5, '위험 요인\n데이터 없음', 
                        ha='center', va='center', transform=ax7.transAxes, 
                        fontsize=12, fontweight='bold')
                ax7.set_title('주요 위험 요인 분포', fontsize=12, fontweight='bold')
        else:
            # 위험 요인이 없는 경우
            ax7.text(0.5, 0.5, '현재 주요\n위험 요인 없음', 
                    ha='center', va='center', transform=ax7.transAxes, 
                    fontsize=12, fontweight='bold', color='green')
            ax7.set_title('주요 위험 요인 분포', fontsize=12, fontweight='bold')
            
            # 원 그리기 (안정 상태 표시)
            circle = plt.Circle((0, 0), 0.3, color='lightgreen', alpha=0.3)
            ax7.add_patch(circle)
        
        plt.tight_layout(pad=3.0)
        plt.savefig('comprehensive_stock_analysis.png', dpi=300, bbox_inches='tight')
        plt.show()
        
        print("✅ 종합 그래프 생성 완료")
        return fig
    
    def display_enhanced_results(self, predictions, current_risks):
        """향상된 결과 출력"""
        print("\n" + "="*100)
        print("                   🎯 KOSPI/KOSDAQ 종합 위험도 분석 및 1주일 예측")
        print("="*100)
        
        # 현재 상황
        if 'kospi' in self.data and 'kosdaq' in self.data:
            current_kospi = self.data['kospi']['Close'].iloc[-1]
            current_kosdaq = self.data['kosdaq']['Close'].iloc[-1]
            
            print(f"\n📊 현재 상황 ({datetime.now().strftime('%Y-%m-%d')}):")
            print(f"   KOSPI: {current_kospi:,.0f}")
            print(f"   KOSDAQ: {current_kosdaq:,.0f}")
            
            if 'technical' in self.patterns:
                tech = self.patterns['technical']
                print(f"   RSI: {tech['rsi']:.0f}")
                print(f"   20일 이평선: {tech['ma20']:,.0f}")
                print(f"   현재 변동성: {tech['volatility']:.1f}%")
        
        # 현재 경제 위험도 상세 분석
        print(f"\n💰 현재 경제 위험도 분석:")
        print("-" * 80)
        
        for risk_type, risk_name in [('inflation', '인플레이션'), ('deflation', '디플레이션'), ('stagflation', '스태그플레이션')]:
            risk_data = current_risks[risk_type]
            risk_level = risk_data['risk']
            
            # 위험도 등급 결정
            if risk_level >= 80:
                level_desc = "🔴 매우 위험"
            elif risk_level >= 60:
                level_desc = "🟡 위험"
            elif risk_level >= 40:
                level_desc = "🟠 주의"
            elif risk_level >= 20:
                level_desc = "🟢 낮음"
            else:
                level_desc = "🔵 매우 낮음"
            
            print(f"   {risk_name:>8}: {risk_level:>5.1f}% {level_desc}")
            
            if risk_data['factors']:
                print(f"     주요 요인: {', '.join(risk_data['factors'][:3])}")
            
            # 미래 예측 정보
            if hasattr(self, 'future_risks') and self.future_risks and risk_type in self.future_risks:
                future_data = self.future_risks[risk_type]
                change = future_data['change']
                trend_arrow = "📈" if change > 2 else "📉" if change < -2 else "➡️"
                
                print(f"     1주일 후 예측: {future_data['predicted']:.1f}% ({change:+.1f}%) {trend_arrow}")
                print(f"     신뢰구간: {future_data['confidence_interval'][0]:.1f}% ~ {future_data['confidence_interval'][1]:.1f}%")
        
        print(f"\n   종합 경제 위험도: {current_risks['overall']:.1f}%")
        
        # 일별 예측 with 위험도
        print(f"\n📈 1주일 일별 예측 (위험도 반영):")
        print("-" * 120)
        print(f"{'날짜':<12} {'요일':<3} {'상태':<4} {'KOSPI':<8} {'KOSDAQ':<8} {'KOSPI변화':<8} {'KOSDAQ변화':<8} {'주요 위험요인':<30}")
        print("-" * 120)
        
        for pred in predictions:
            if pred['status'] == '휴장':
                print(f"{pred['date']} {pred['weekday']} 휴장")
            else:
                risk_summary = ', '.join(pred.get('risk_factors', [])[:2])
                if not risk_summary:
                    risk_summary = "위험요인 없음"
                
                print(f"{pred['date']} {pred['weekday']} 개장 "
                      f"{pred['kospi']:>7.0f} "
                      f"{pred['kosdaq']:>7.0f} "
                      f"{pred['kospi_change']:>+6.1f}% "
                      f"{pred['kosdaq_change']:>+6.1f}% "
                      f"{risk_summary:<30}")
        
        # 주간 요약
        trading_days = [p for p in predictions if p['status'] == '개장']
        if trading_days:
            total_kospi_change = sum(p['kospi_change'] for p in trading_days)
            total_kosdaq_change = sum(p['kosdaq_change'] for p in trading_days)
            
            print(f"\n📊 주간 요약:")
            print(f"   KOSPI 누적 변화: {total_kospi_change:+.2f}%")
            print(f"   KOSDAQ 누적 변화: {total_kosdaq_change:+.2f}%")
            
            # 주간 평균 위험도
            week_avg_inflation = np.mean([p['economic_risks']['inflation']['risk'] for p in trading_days])
            week_avg_deflation = np.mean([p['economic_risks']['deflation']['risk'] for p in trading_days])
            week_avg_stagflation = np.mean([p['economic_risks']['stagflation']['risk'] for p in trading_days])
            
            print(f"   주간 평균 인플레이션 위험도: {week_avg_inflation:.1f}%")
            print(f"   주간 평균 디플레이션 위험도: {week_avg_deflation:.1f}%")
            print(f"   주간 평균 스태그플레이션 위험도: {week_avg_stagflation:.1f}%")
        
        # 위험도 변화 트렌드 분석
        if hasattr(self, 'future_risks') and self.future_risks:
            print(f"\n🔮 1주일 후 위험도 변화 전망:")
            print("-" * 80)
            
            for risk_type, risk_name in [('inflation', '인플레이션'), ('deflation', '디플레이션'), ('stagflation', '스태그플레이션')]:
                if risk_type in self.future_risks:
                    future_data = self.future_risks[risk_type]
                    current_val = future_data['current']
                    predicted_val = future_data['predicted']
                    change = future_data['change']
                    trend = future_data['trend']
                    
                    trend_icon = {"increasing": "📈", "decreasing": "📉", "stable": "➡️"}[trend]
                    
                    print(f"   {risk_name:>8}: {current_val:.1f}% → {predicted_val:.1f}% ({change:+.1f}%) {trend_icon}")
                    print(f"     변동성: {future_data['volatility']:.1f}% | 추세: {trend}")
        
        # 투자 권장사항 (위험도 기반)
        overall_risk = current_risks['overall']
        
        print(f"\n💡 투자 전략 권장사항:")
        print("-" * 60)
        
        if overall_risk > 70:
            print("   🔴 고위험 상황 - 매우 보수적 전략 권장")
            print("     • 현금 비중 50% 이상 확대")
            print("     • 방어주 중심 포트폴리오")
            print("     • 헤지 상품 고려")
        elif overall_risk > 50:
            print("   🟡 중위험 상황 - 신중한 투자 전략")
            print("     • 현금 비중 30-40% 유지")
            print("     • 우량주 중심 투자")
            print("     • 분산투자 강화")
        elif overall_risk > 30:
            print("   🟠 보통 위험 - 균형잡힌 포트폴리오")
            print("     • 현금 비중 20-30%")
            print("     • 성장주/가치주 균형")
            print("     • 정기 리밸런싱")
        else:
            print("   🟢 저위험 상황 - 적극적 투자 가능")
            print("     • 성장주 비중 확대")
            print("     • 테마주 투자 고려")
            print("     • 레버리지 상품 제한적 활용")
        
        # 주요 모니터링 지표
        print(f"\n📋 주요 모니터링 지표:")
        monitoring_indicators = []
        
        if current_risks['inflation']['risk'] > 50:
            monitoring_indicators.extend(["원자재 가격", "달러 인덱스", "장기 금리"])
        if current_risks['deflation']['risk'] > 50:
            monitoring_indicators.extend(["VIX 지수", "글로벌 주가", "경기선행지수"])
        if current_risks['stagflation']['risk'] > 40:
            monitoring_indicators.extend(["환율", "공급망 지표", "임금 상승률"])
        
        if not monitoring_indicators:
            monitoring_indicators = ["전반적 시장 동향", "기술적 지표", "거래량"]
        
        for i, indicator in enumerate(set(monitoring_indicators)[:5], 1):
            print(f"   {i}. {indicator}")
        
        print("\n⚠️ 주의사항:")
        print("   • 이 분석은 과거 데이터 기반 통계 모델입니다")
        print("   • 예상치 못한 이벤트(지정학적 리스크, 정책 변화 등)는 반영되지 않습니다")
        print("   • 실제 투자 결정 시 최신 뉴스와 추가 분석이 필요합니다")
        print("   • 위험도 예측의 신뢰구간을 고려하여 유연한 전략을 수립하세요")
        
        print("="*100)

def main():
    """메인 실행 함수"""
    print("🚀 종합 주식 예측 시스템 (위험도 분석 강화버전) 시작...")
    print("="*80)
    
    try:
        # 시스템 초기화
        predictor = EnhancedStockPredictor(start_date='2015-01-01')
        
        # 전체 프로세스 실행
        print("1️⃣ 데이터 수집...")
        data = predictor.collect_all_data()
        
        print("2️⃣ 패턴 분석...")
        patterns = predictor.analyze_patterns()
        
        print("3️⃣ 이상치 감지...")
        anomalies = predictor.detect_anomalies()
        
        print("4️⃣ 상세 경제 위험도 분석...")
        current_risks = predictor.calculate_economic_risks_detailed()
        
        print("5️⃣ 향상된 1주일 예측...")
        predictions = predictor.predict_weekly_enhanced()
        
        print("6️⃣ 종합 시각화 생성...")
        fig = predictor.create_comprehensive_visualizations(predictions, current_risks)
        
        # 결과 출력
        predictor.display_enhanced_results(predictions, current_risks)
        
        print("\n🎉 시스템 실행 완료!")
        print("📊 상세 그래프가 'comprehensive_stock_analysis.png'로 저장되었습니다.")
        
    except Exception as e:
        print(f"❌ 시스템 실행 중 오류: {e}")
        import traceback
        traceback.print_exc()
        print("💡 인터넷 연결을 확인하고 다시 시도해주세요.")

if __name__ == "__main__":
    main()
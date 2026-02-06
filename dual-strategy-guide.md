# Dual-Strategy Algorithmic Trading System - Technical Summary

**Date:** February 6, 2026  
**Portfolio Strategy:** Two-pronged approach combining medium-term news sentiment with long-term fundamental analysis  
**Test Capital:** £50,000 (paper trading)

---

## Portfolio Architecture Overview

This system implements a **satellite-core strategy** used by professional quant funds:

```
┌─────────────────────────────────────────────────────────────┐
│                    £50,000 PORTFOLIO                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  SATELLITE (20% = £10,000)          CORE (80% = £40,000)   │
│  Medium-Term News Strategy          Long-Term Fundamental   │
│  ↓                                   ↓                      │
│  News → LLM Sentiment → Trade       Quarterly SEC Filings  │
│  Hold: 5 days - 13 weeks            → LLM Analysis         │
│  Frequency: 10-20 trades/month      → Quality Scoring      │
│  Goal: Exploit news mispricing      Hold: 1-3 years        │
│  Expected: 15-25% annual return     Frequency: Quarterly    │
│  Risk: Higher volatility            Goal: Capture value     │
│                                      Expected: 8-12% return │
│                                      Risk: Lower volatility │
└─────────────────────────────────────────────────────────────┘
```

**Why This Approach:**
- **Diversification:** News and fundamentals are uncorrelated alpha sources
- **Risk Management:** If news algo fails, 80% of capital protected
- **Proven:** Federal Reserve research shows news predictability lasts up to 13 weeks
- **Cost-Efficient:** Fundamental analysis is 100× cheaper than frequent trading

---

## Strategy 1: Medium-Term News Sentiment (Satellite)

### Architecture

```
Real-time Financial News Feed
    ↓
News API (Alpaca News + marketaux)
    ↓
Sentiment Analysis & Signal Generation
    ↓
Google Gemini 2.5 Flash-Lite API
    ↓
Trade Decision (BUY/SELL/HOLD with conviction score)
    ↓
Alpaca Paper Trading API (Testing Phase)
    ↓
Alpaca Live Trading API (Production)
```

### Strategy Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Capital Allocation** | £10,000 (20% of portfolio) | Higher risk tolerance for alpha generation |
| **Position Size** | £500-1,000 per trade | 5-10% of satellite capital, 1-2% of total |
| **Holding Period** | 5-65 trading days (1-13 weeks) | Federal Reserve study shows 13-week predictability window |
| **Trading Frequency** | 10-20 new positions/month | Balances opportunity with transaction costs |
| **Max Open Positions** | 8-12 concurrent | Diversification within satellite strategy |
| **Stop Loss** | 2-3% per position | Automatic risk management |

### Key Research Backing

**Academic Evidence:**
- **OPT (GPT-3) Study (2024):** 74.4% sentiment accuracy, 355% gain over 2 years, Sharpe ratio 3.05
- **Federal Reserve Study:** News sentiment predicts returns for up to 13 weeks; negative sentiment especially persistent
- **SSRN Study (2024):** Weekly aggregation of news dramatically increases predictability

**Why Medium-Term (Not Day Trading):**
- Day traders average **-3.8% annual returns** after costs
- Swing traders average **+2.1% annual returns**
- Markets take time to fully absorb news—the edge exists in the 5-day to 13-week window
- Transaction costs 10× lower than high-frequency trading

---

## Strategy 2: Long-Term Fundamental Analysis (Core)

### Architecture

```
Quarterly SEC Filings (10-Q, 10-K)
    ↓
EDGAR API (Free) + Financial Data APIs
    ↓
Document Parsing & Extraction
    ↓
Google Gemini 2.5 Pro API (Better reasoning for fundamentals)
    ↓
Structured Analysis: Revenue, Margins, Cash Flow, Debt, Moat
    ↓
Scoring & Ranking (1-10 scale)
    ↓
Portfolio Construction: Hold top 30-50 stocks
    ↓
Quarterly Rebalancing (sell bottom quartile, buy top-rated)
```

### Strategy Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Capital Allocation** | £40,000 (80% of portfolio) | Lower risk core holdings |
| **Analysis Universe** | 500 stocks quarterly | Screen mid-large cap US stocks |
| **Holdings** | 30-50 stocks | Concentrated but diversified |
| **Position Size** | £800-1,300 per stock | Equal-weighted or conviction-weighted |
| **Holding Period** | 1-3 years typical | Long-term value realization |
| **Rebalancing** | Quarterly | Aligned with earnings cycle |
| **Turnover** | 15-25% per quarter | Only trade when conviction changes |

### LLM Fundamental Analysis Prompt

```python
prompt = f"""
Analyze this 10-Q filing for {ticker} (Quarter ending {date}):

{sec_filing_text}

Provide structured JSON analysis:
{{
  "revenue_trend": {{
    "yoy_growth": float,
    "sustainability": "improving/stable/declining",
    "drivers": [list key factors]
  }},
  "profitability": {{
    "gross_margin": float,
    "operating_margin": float,
    "net_margin": float,
    "trend": "expanding/stable/contracting"
  }},
  "cash_flow": {{
    "operating_cf": float,
    "free_cf": float,
    "quality_score": int (1-10)
  }},
  "balance_sheet": {{
    "debt_to_equity": float,
    "current_ratio": float,
    "health_score": int (1-10)
  }},
  "competitive_position": {{
    "moat_strength": "wide/narrow/none",
    "competitive_advantages": [list],
    "threats": [list]
  }},
  "quarter_over_quarter_changes": {{
    "material_improvements": [list],
    "material_deteriorations": [list]
  }},
  "overall_score": int (1-10),
  "recommendation": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
  "confidence": float (0-1),
  "reasoning": string (2-3 sentences)
}}

Focus on material changes from prior quarter. Be objective and data-driven.
"""
```

### Why Quarterly Fundamental Analysis

**Cost Advantage:**
- 500 stocks × 4 quarters = 2,000 analyses/year
- At 52,000 tokens each = 104M tokens annually
- **Gemini 2.5 Pro cost: ~£130/year** (vs £180/year for 20 news trades/month)
- Essentially free compared to frequent trading

**Quality Advantage:**
- Use premium model (Gemini Pro) for better reasoning
- No latency constraints—can take 30 seconds per analysis
- Overnight batch processing quarterly

---

## Trading API: Alpaca

**Why Alpaca:**
- ✅ **Free paper trading** with $100,000 virtual balance
- ✅ **Commission-free** US stock trading ($0 per trade)
- ✅ Real-time IEX market data included
- ✅ Full REST API + Python SDK
- ✅ Supports fractional shares, margin, short selling
- ✅ Simple setup - email only, no funding required for testing
- ✅ Built-in news API included

**Setup:**
```python
from alpaca.trading.client import TradingClient

# Paper trading (testing)
paper_client = TradingClient(
    api_key='YOUR_KEY',
    secret_key='YOUR_SECRET',
    paper=True
)

# Live trading (production)
live_client = TradingClient(
    api_key='YOUR_KEY',
    secret_key='YOUR_SECRET',
    paper=False
)
```

**Markets Supported:** US equities, ETFs, cryptocurrencies

**Alternative:** Interactive Brokers (for global markets, but more complex setup)

---

## LLM APIs

### For Medium-Term News Strategy: Gemini 2.5 Flash-Lite

**Why Flash-Lite:**
- **Most cost-effective:** $0.10 per 1M input tokens, $0.40 per 1M output tokens
- Fast inference (<2 seconds)—critical for news trading
- Sufficient for sentiment analysis
- 50% cheaper than GPT-4o mini

**Pricing Comparison:**

| Model | Input/1M tokens | Output/1M tokens | Cost per Decision |
|-------|----------------|------------------|-------------------|
| **Gemini 2.5 Flash-Lite** | $0.10 | $0.40 | **$0.00033** |
| GPT-4o mini | $0.15 | $0.60 | $0.00050 |
| GPT-4o | $2.50 | $10.00 | $0.00825 |

### For Long-Term Fundamental Strategy: Gemini 2.5 Pro

**Why Pro:**
- Better reasoning for complex financial analysis
- Larger context window for lengthy SEC filings
- Quality matters more than speed for quarterly decisions
- Still very affordable at quarterly frequency

**Pricing:**
- Input: $1.25 per 1M tokens
- Output: $10.00 per 1M tokens

---

## Comprehensive Cost Analysis

### Medium-Term News Strategy (20 trades/month)

**Assumptions:**
- £10,000 satellite capital
- 20 new trades per month = 240 trades/year
- Average position: £500-1,000
- News article: ~1,300 tokens input
- LLM response: ~500 tokens output
- Total: ~1,800 tokens per trade decision

**Annual Costs:**

| Cost Component | Per Trade | Annual (240 trades) |
|---------------|-----------|---------------------|
| **LLM API (Gemini Flash-Lite)** | £0.75 | £180 |
| **Trading Commissions (Alpaca)** | £0.40 | £96 |
| **News API (marketaux)** | - | £108 |
| **SUBTOTAL** | £1.15 | £384 |

**Break-even per trade:** Need >£1.15 profit per trade (0.12-0.23% return per trade)

### Long-Term Fundamental Strategy (500 stocks analyzed quarterly)

**Assumptions:**
- £40,000 core capital
- Analyze 500 stocks each quarter
- Hold 30-50 top-rated stocks
- Rebalance quarterly (~30 trades per quarter, 120 trades/year)
- SEC filing: ~50,000 tokens input per stock
- LLM analysis: ~2,000 tokens output per stock

**Annual Costs:**

| Cost Component | Per Quarter | Annual (4 quarters) |
|---------------|-------------|---------------------|
| **LLM API (Gemini Pro)** | £32.50 | £130 |
| **Trading Commissions** | £20 | £80 |
| **Financial Data API** | £0 | £0 (EDGAR is free) |
| **SUBTOTAL** | £52.50 | £210 |

### Combined Portfolio Annual Costs

| Strategy | Annual Operating Cost | As % of Allocated Capital |
|----------|----------------------|---------------------------|
| **Medium-Term News (£10k)** | £384 | 3.84% |
| **Long-Term Fundamental (£40k)** | £210 | 0.53% |
| **TOTAL (£50k portfolio)** | **£594** | **1.19%** |

**Total cost: 1.19% of portfolio annually**—extremely reasonable for active management with cutting-edge LLM analysis.

**Comparison:** Traditional hedge funds charge 2% management + 20% performance fees.

---

## Hardware Requirements

### You DO NOT Need a GPU

**All LLM inference runs on Google's servers via API.**

**Minimal Configuration (sufficient for both strategies):**
- **CPU:** Intel i5-13xxx or AMD Ryzen 5 7xxx (4-8 cores)
- **RAM:** 16GB DDR4
- **Storage:** 256GB SSD
- **Network:** Stable broadband (10+ Mbps)
- **OS:** Linux (Ubuntu), macOS, or Windows

**Cost:** £600-800 (or use existing development machine)

**Why minimal specs work:**
- LLM inference is via API (zero local GPU computation)
- News strategy: lightweight real-time processing
- Fundamental strategy: batch processing overnight quarterly
- Trading execution via API calls
- Backtesting doesn't require GPU

### If Running LLMs Locally (NOT Recommended)

**Only consider if:**
- You need >1,000 inferences/day (API costs >£260/day)
- You want complete data privacy
- You're experimenting with custom fine-tuned models

**Local Setup (7B model):**
- GPU: RTX 4060 Ti 16GB (~£500)
- CPU: i7-13700K (~£350)
- RAM: 32GB DDR5 (~£120)
- **Total:** £1,500-2,000

**Cost comparison:** £1,500 upfront vs £310/year API costs = break-even at ~5 years (not worth it)

---

## Data Sources

### News Data (Medium-Term Strategy)

| Provider | Free Tier | Paid Plans | Real-time | Coverage |
|----------|-----------|------------|-----------|----------|
| **Alpaca News** | Included with API | Free | Yes | US market news |
| **marketaux** | 100 req/day | From $9/mo | Yes | Stocks, crypto, forex |
| **NewsAPI** | 100 req/day | From $449/mo | No (24h delay) | General news |
| **Finage** | Trial available | From $29/mo | Yes | Financial news + data |

**Recommendation:** Start with **Alpaca News (free)** + **marketaux ($9/mo)** for broader coverage.

### Fundamental Data (Long-Term Strategy)

| Provider | Cost | Coverage | Use Case |
|----------|------|----------|----------|
| **EDGAR API** | Free | All SEC filings | 10-Q, 10-K, 8-K filings |
| **Financial Modeling Prep** | Free tier: 250 req/day | Financial statements, ratios | Structured financial data |
| **Alpha Vantage** | Free tier: 25 req/day | Fundamentals, price data | Supplementary data |
| **yfinance (Python)** | Free | Yahoo Finance data | Price history, basic fundamentals |

**Recommendation:** **EDGAR API (free)** for SEC filings + **Financial Modeling Prep (free tier)** for structured data.

---

## Implementation Roadmap

### Phase 1: Medium-Term News Strategy (Months 1-3)

**Why start here:**
- Higher complexity—needs more iteration
- Higher risk—validate before deploying capital
- More active management—build monitoring infrastructure

**Tasks:**
- [ ] Sign up for Alpaca paper trading account
- [ ] Obtain Google Gemini API key (Flash-Lite)
- [ ] Set up Python development environment (3.10+)
- [ ] Install dependencies: `alpaca-py`, `google-generativeai`, `pandas`, `numpy`
- [ ] Implement news fetching module (Alpaca News API)
- [ ] Build LLM sentiment analysis pipeline
- [ ] Create trading logic with 13-week holding period logic
- [ ] Implement risk management (stop losses, position sizing)
- [ ] Backtest on historical news data (2023-2025)
- [ ] Deploy to paper trading environment
- [ ] Monitor for 30-60 days with £10,000 virtual capital

**Success Criteria:**
- Win rate >55%
- Average profit per trade >£1.50 (>0.15% return)
- Maximum drawdown <15% of satellite capital
- Sharpe ratio >1.0

### Phase 2: Long-Term Fundamental Strategy (Months 4-6)

**Once news strategy is stable:**

**Tasks:**
- [ ] Set up EDGAR API access (free, just need user agent)
- [ ] Obtain Google Gemini API key (Pro tier)
- [ ] Build SEC filing parser (10-Q, 10-K extraction)
- [ ] Design LLM fundamental analysis prompt
- [ ] Create scoring and ranking system
- [ ] Build portfolio construction logic (top 30-50 stocks)
- [ ] Test on historical filings (Q1 2024 - Q4 2025)
- [ ] Run first quarterly analysis batch (500 stocks)
- [ ] Select initial 30 stocks, deploy to paper trading
- [ ] Monitor for one full quarter

**Success Criteria:**
- Successfully parse and analyze 500 stocks in <8 hours
- LLM scores correlate with subsequent stock performance
- Portfolio construction produces reasonable diversification
- No major technical issues in batch processing

### Phase 3: Live Deployment (Months 7-9)

**Gradual capital deployment:**

**Month 7:**
- Deploy £2,000 to news strategy (20% of target)
- Deploy £8,000 to fundamental strategy (20% of target)
- Monitor daily for news strategy, weekly for fundamental

**Month 8:**
- If performance acceptable, scale to £5,000 news / £20,000 fundamental (50% of target)
- Refine prompts based on live trading results

**Month 9:**
- Scale to full £10,000 / £40,000 if performance meets targets
- Implement automated monitoring and alerting

### Phase 4: Optimization & Scaling (Months 10-12)

**Tasks:**
- [ ] Analyze 6+ months of live trading data
- [ ] Refine LLM prompts based on performance
- [ ] Optimize position sizing and risk management
- [ ] Consider expanding fundamental analysis to 1,000 stocks
- [ ] Explore additional alpha signals (technical indicators, options flow)
- [ ] Build comprehensive performance dashboard
- [ ] Document lessons learned and edge cases

---

## Performance Targets

### Medium-Term News Strategy

**Minimum Viability (to continue running):**
- Win rate: >55%
- Average profit per trade: >£1.50 (>0.15% return)
- Maximum drawdown: <20% of satellite capital
- Sharpe ratio: >1.0
- Monthly return: >1% (>12% annualized)

**Aspirational (based on academic studies):**
- Win rate: >65%
- Average profit per trade: >£5 (>0.5% return)
- Maximum drawdown: <15%
- Sharpe ratio: >2.0
- Annual return: >20%

### Long-Term Fundamental Strategy

**Minimum Viability:**
- Annual return: >8% (beat S&P 500)
- Maximum drawdown: <25%
- Sharpe ratio: >0.8
- Positive returns in 3 out of 4 years

**Aspirational:**
- Annual return: >12%
- Maximum drawdown: <20%
- Sharpe ratio: >1.2
- Consistent outperformance of broad market indices

### Combined Portfolio

**Target:** 12-15% annual return with Sharpe ratio >1.5

**Risk Budget:**
- News strategy drawdown: <20% of £10k = £2,000 loss maximum
- Fundamental strategy drawdown: <25% of £40k = £10,000 loss maximum
- Combined worst-case: £12,000 loss (24% portfolio drawdown)

---

## Risk Management

### Position-Level Risk Management

**News Strategy:**
1. **Position Sizing:** £500-1,000 per trade (5-10% of satellite capital)
2. **Stop Loss:** Automatic 2-3% stop on all positions
3. **Take Profit:** Consider scaling out at 5-7% gains
4. **Maximum Hold:** Close positions after 13 weeks regardless of P&L
5. **Maximum Open Positions:** 8-12 concurrent

**Fundamental Strategy:**
1. **Position Sizing:** £800-1,300 per stock (equal or conviction-weighted)
2. **Stop Loss:** 15-20% stop loss (allow for volatility in quality names)
3. **Rebalancing:** Quarterly review, sell bottom quartile performers
4. **Concentration Limits:** No single stock >5% of core portfolio
5. **Sector Limits:** No single sector >25% of core portfolio

### Portfolio-Level Risk Management

1. **Daily Loss Limit (News):** Stop trading if satellite capital down >5% in a day
2. **Monthly Loss Limit (News):** Pause and review if down >10% in a month
3. **Quarterly Review (Fundamental):** Full portfolio review every quarter
4. **Correlation Monitoring:** Ensure news and fundamental strategies remain uncorrelated
5. **Leverage:** None—stay fully cash-funded

### Operational Risk Management

1. **API Failures:** Implement retry logic and fallback mechanisms
2. **Data Quality:** Validate all news and SEC filing data before processing
3. **Model Degradation:** Track LLM accuracy over time, A/B test prompt changes
4. **Circuit Breakers:** Automatic halting if unusual market conditions detected
5. **Audit Logging:** Log all trades, decisions, and system events for review

---

## Tech Stack Summary

| Component | Technology | Cost | Used By |
|-----------|-----------|------|---------|
| **Programming Language** | Python 3.10+ | Free | Both strategies |
| **Trading API** | Alpaca Trading API | Free (commission-free) | Both strategies |
| **LLM API (News)** | Google Gemini 2.5 Flash-Lite | £180/year | News strategy |
| **LLM API (Fundamental)** | Google Gemini 2.5 Pro | £130/year | Fundamental strategy |
| **News Data** | Alpaca News + marketaux | £108/year | News strategy |
| **Fundamental Data** | EDGAR API (free) | Free | Fundamental strategy |
| **Infrastructure** | Standard PC/laptop | £0 (existing) | Both strategies |
| **Deployment** | Local or cloud (AWS t3.small) | £0-30/month | Both strategies |

**Total Annual Operating Cost:** £594 (1.19% of portfolio)

---

## Monitoring & Alerting

### Daily Monitoring (News Strategy)

**Automated checks:**
- New positions opened today
- Positions closed today with P&L
- Current open positions and unrealized P&L
- Stop losses triggered
- Daily P&L vs target

**Manual review (5-10 minutes/day):**
- Review LLM sentiment decisions for reasonableness
- Check for unusual market conditions
- Verify all trades executed correctly

### Weekly Monitoring (Both Strategies)

**News strategy:**
- Weekly P&L and win rate
- Position holding periods
- LLM prompt performance
- API costs and rate limits

**Fundamental strategy:**
- Portfolio composition changes
- Individual stock performance vs scoring
- Sector allocation drift

### Monthly Monitoring

**Comprehensive review:**
- Monthly returns vs benchmarks
- Sharpe ratio and maximum drawdown
- Transaction costs and slippage
- LLM API costs vs budget
- Strategy correlation analysis

### Quarterly Monitoring (Fundamental Strategy)

**Full rebalancing process:**
- Batch analyze 500 stocks
- Review current holdings' scores
- Identify candidates for selling (bottom quartile)
- Identify candidates for buying (top quartile, not currently held)
- Execute rebalancing trades
- Document rationale for major changes

---

## Regulatory & Tax Considerations (UK)

### Trading Regulations

- UK residents can trade US stocks via Alpaca (broker is US-regulated)
- Ensure your broker relationship complies with FCA guidelines
- Consider if you need to register as a professional trader (unlikely at £50k scale)

### Tax Obligations

**Capital Gains Tax:**
- Annual CGT allowance: £3,000 (2026-27 tax year)
- Gains above £3,000 taxed at 10% (basic rate) or 20% (higher rate)
- Applies to both strategies—aggregate all gains/losses

**Record Keeping:**
- Keep detailed logs of all trades (date, ticker, quantity, price, fees)
- Track cost basis accurately (HMRC requires this)
- Maintain LLM decision logs as supporting documentation
- Report on Self Assessment tax return annually

**Recommended:**
- Use accounting software or Python script to track all trades
- Export monthly P&L reports for HMRC
- Consider consulting a tax advisor if profits significant

---

## Latency & Performance Requirements

### News Strategy (Time-Sensitive)

**Target latency:**
- News published → News fetched: <10 seconds
- News fetched → LLM analysis: <2 seconds
- LLM decision → Trade executed: <1 second
- **Total: <15 seconds** from news to execution

**Your setup:**
- Alpaca News API: Near real-time (~5 seconds)
- Gemini Flash-Lite: ~1-2 seconds inference
- Alpaca Trading API: ~0.5 seconds execution
- **Estimated total: ~8-10 seconds** ✅

### Fundamental Strategy (Not Time-Sensitive)

**Batch processing (quarterly):**
- Fetch 500 SEC filings: ~10-20 minutes
- Process 500 filings through LLM: ~3-8 hours (can run overnight)
- Generate scores and rankings: ~1-2 minutes
- Execute rebalancing trades: ~5-10 minutes

**No latency concerns—quality and accuracy are far more important than speed.**

---

## Common Pitfalls & How to Avoid Them

### For News Strategy

❌ **Over-optimization on backtests**
- ✅ Use walk-forward validation, not curve-fitting
- ✅ Test on multiple time periods (bull, bear, sideways markets)

❌ **Ignoring transaction costs**
- ✅ Include commissions, slippage, and LLM costs in backtest

❌ **Chasing too many signals**
- ✅ Focus on high-conviction trades only (>70% LLM confidence)

❌ **Not using stop losses**
- ✅ Mandatory 2-3% stops on all positions

❌ **Position sizing too large**
- ✅ Never >2% of total portfolio per trade

### For Fundamental Strategy

❌ **Overfitting to recent winners**
- ✅ Evaluate scoring system on 5+ year historical data

❌ **Ignoring qualitative factors**
- ✅ LLM should assess management, competitive moat, not just numbers

❌ **Too much turnover**
- ✅ Only trade when conviction meaningfully changes (threshold: score change >2 points)

❌ **Sector concentration**
- ✅ Maintain diversification across 8+ sectors

❌ **Neglecting rebalancing**
- ✅ Stick to quarterly discipline even when busy

---

## Success Metrics & KPIs

### Must Track (Both Strategies)

| Metric | Target | Measurement Frequency |
|--------|--------|----------------------|
| **Total Return** | >12% annual | Monthly |
| **Sharpe Ratio** | >1.5 | Quarterly |
| **Maximum Drawdown** | <20% | Continuous |
| **Win Rate** | >55% (news), >60% (fundamental) | Monthly |
| **Average Profit per Trade** | >£2 (news), >£50 (fundamental) | Monthly |
| **Transaction Costs as % of Profit** | <5% | Monthly |
| **LLM API Costs as % of Profit** | <2% | Monthly |

### Advanced Metrics

| Metric | Purpose |
|--------|---------|
| **Alpha vs S&P 500** | Measure skill vs market return |
| **Beta** | Measure systematic risk exposure |
| **Calmar Ratio** | Return / maximum drawdown |
| **Sortino Ratio** | Return / downside deviation (better than Sharpe for skewed returns) |
| **Information Ratio** | Alpha / tracking error |

---

## Next Steps

### Week 1: Environment Setup
1. Create Alpaca paper trading account
2. Obtain Google Gemini API keys (Flash-Lite and Pro)
3. Set up Python environment with required libraries
4. Test API connectivity and basic functionality

### Week 2-4: Build News Strategy
1. Implement news fetching and parsing
2. Design and test LLM sentiment prompts
3. Build position management and risk management logic
4. Backtest on 2024-2025 news data

### Week 5-8: Paper Trade News Strategy
1. Deploy to Alpaca paper trading
2. Monitor daily and refine prompts
3. Track performance metrics
4. Document edge cases and failures

### Week 9-12: Build Fundamental Strategy
1. Set up EDGAR API integration
2. Build SEC filing parser
3. Design fundamental analysis prompts
4. Test on historical quarterly data

### Month 4-6: Paper Trade Both Strategies
1. Run both strategies simultaneously in paper trading
2. Validate cost projections
3. Ensure strategies are uncorrelated
4. Prepare for live deployment

### Month 7+: Live Deployment & Optimization
1. Gradual capital deployment (20% → 50% → 100%)
2. Continuous monitoring and prompt refinement
3. Quarterly fundamental rebalancing
4. Monthly performance reviews

---

## Critical Success Factors

1. **Prompt Engineering > Infrastructure:** Spend 80% of time refining LLM prompts, 20% on infrastructure
2. **Risk Management > Returns:** Protecting capital is more important than maximizing gains
3. **Discipline > Emotions:** Follow the system, don't override based on gut feelings
4. **Validation > Theory:** Trust backtests and paper trading results, not assumptions
5. **Simplicity > Complexity:** Start simple, add complexity only when proven necessary

---

## Resources & Community

### Documentation
- **Alpaca API:** https://alpaca.markets/docs/
- **Gemini API:** https://ai.google.dev/docs
- **EDGAR API:** https://www.sec.gov/edgar/sec-api-documentation
- **Financial Modeling Prep:** https://site.financialmodelingprep.com/developer/docs

### Python Libraries
```bash
pip install alpaca-py google-generativeai pandas numpy requests sec-edgar-downloader yfinance
```

### Communities
- **r/algotrading** - Reddit community for algorithmic traders
- **Alpaca Community Slack** - Direct access to Alpaca team and other traders
- **QuantConnect Forum** - Quantitative trading discussions
- **Bogleheads Forum** - Long-term investing perspectives

### Academic Research
- Federal Reserve: "News versus Sentiment: Predicting Stock Returns from News Stories"
- ArXiv: "Sentiment trading with large language models" (2024)
- ArXiv: "Impact of LLMs news Sentiment Analysis on Stock Price Predictions" (2025)

---

## Appendix: Example Code Snippets

### News Strategy: Fetch and Analyze

```python
from alpaca.data.historical import NewsClient
import google.generativeai as genai

# Initialize clients
news_client = NewsClient(api_key='YOUR_KEY', secret_key='YOUR_SECRET')
genai.configure(api_key='YOUR_GEMINI_KEY')
model = genai.GenerativeModel('gemini-2.5-flash-lite')

# Fetch latest news
news = news_client.get_news(symbols=['AAPL'], limit=10)

for article in news:
    prompt = f"""
    Analyze this financial news article and provide a trading signal:
    
    Title: {article.headline}
    Content: {article.summary}
    
    Provide:
    1. Sentiment: BULLISH/NEUTRAL/BEARISH
    2. Conviction: 0-100
    3. Time horizon: SHORT (1-5 days), MEDIUM (1-4 weeks), LONG (1-3 months)
    4. Reasoning: 2-3 sentences
    
    Format as JSON.
    """
    
    response = model.generate_content(prompt)
    signal = parse_json(response.text)
    
    if signal['conviction'] > 70 and signal['sentiment'] == 'BULLISH':
        # Place buy order
        pass
```

### Fundamental Strategy: SEC Filing Analysis

```python
from sec_edgar_downloader import Downloader
import google.generativeai as genai

# Download SEC filing
dl = Downloader("MyCompany", "my@email.com")
dl.get("10-Q", "AAPL", after="2025-01-01", before="2025-12-31")

# Read filing
with open("sec_filings/AAPL/10-Q/0001234567.txt", "r") as f:
    filing_text = f.read()

# Analyze with LLM
genai.configure(api_key='YOUR_GEMINI_KEY')
model = genai.GenerativeModel('gemini-2.5-pro')

prompt = f"""
Analyze this 10-Q filing for AAPL:

{filing_text}

Provide structured JSON analysis covering:
- revenue_trend
- profitability
- cash_flow
- balance_sheet
- competitive_position
- overall_score (1-10)
- recommendation (BUY/HOLD/SELL)

Be objective and data-driven.
"""

response = model.generate_content(prompt)
analysis = parse_json(response.text)

# Score and rank
if analysis['overall_score'] >= 8:
    # Add to buy list
    pass
```

---

**Document Version:** 2.0  
**Last Updated:** February 6, 2026  
**Changes from v1.0:** Added dual-strategy architecture, long-term fundamental analysis system, comprehensive cost analysis for both strategies, updated implementation roadmap
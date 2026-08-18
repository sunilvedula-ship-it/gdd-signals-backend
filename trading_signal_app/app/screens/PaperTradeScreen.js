import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, ScrollView, TouchableOpacity, ActivityIndicator, TextInput, useWindowDimensions } from 'react-native';
import { BACKEND_URL } from '../config';

const formatSignalDate = (isoString) => {
  if (!isoString) return '';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '';
  const day = String(date.getDate()).padStart(2, '0');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const month = months[date.getMonth()];
  const year = date.getFullYear();
  let hours = date.getHours();
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  hours = hours ? hours : 12;
  const hoursStr = String(hours).padStart(2, '0');
  return `${day}-${month}-${year} ${hoursStr}:${minutes} ${ampm}`;
};

const getNormalizedBaseSymbol = (symbol) => {
  let sym = (symbol || '').toUpperCase().trim();
  if (sym.includes(':')) {
    sym = sym.split(':').pop();
  }
  const parts = sym.split(/\s+/);
  if (parts.length >= 3 && ['CE', 'PE'].includes(parts[parts.length - 1])) {
    sym = parts[0];
  }
  if (sym.endsWith('1!')) {
    sym = sym.slice(0, -2);
  } else if (sym.endsWith('!')) {
    sym = sym.slice(0, -1);
  }
  if (sym.includes('XAU') || sym.includes('GOLD')) return 'GOLD';
  if (sym.includes('SILVER')) return 'SILVER';
  if (sym.includes('BANKNIFTY') || sym.includes('BNF')) return 'BANKNIFTY';
  if (sym.includes('NIFTY')) return 'NIFTY';
  if (sym.includes('SENSEX') || sym.includes('BSX')) return 'SENSEX';
  if (sym.includes('CRUDE')) return 'CRUDEOIL';
  return sym;
};

const getLotSize = (symbol) => {
  const sym = (symbol || '').toUpperCase();
  if (sym.includes('BANKNIFTY')) return 30;
  if (sym.includes('NIFTY')) return 65;
  if (sym.includes('SENSEX')) return 20;
  if (sym.includes('CRUDE')) return 100;
  if (sym.includes('GOLD')) return 100;
  if (sym.includes('WIPRO')) return 1500;
  if (sym.includes('RELIANCE')) return 250;
  if (sym.includes('TITAN')) return 375;
  if (sym.includes('BAJFINSERV')) return 500;
  if (sym.includes('ADANIPORTS')) return 625;
  return 100;
};

const isCryptoAsset = (symbol) => {
  const sym = (symbol || '').toUpperCase();
  if (sym.includes('GOLD') || sym.includes('XAU') || sym.includes('CRUDE') || sym.includes('NIFTY') || sym.includes('SENSEX') || sym.includes('BSX')) {
    return false;
  }
  return sym.includes('BTC') || sym.includes('ETH') || sym.includes('SOL') || sym.includes('USDT');
};

const getSubgroup = (symbol) => {
  const sym = (symbol || '').toUpperCase();
  const isOption = sym.endsWith(' CE') || sym.endsWith(' PE') || sym.includes(' CE') || sym.includes(' PE');
  const isIndex = sym.includes('NIFTY') || sym.includes('SENSEX') || sym.includes('BSX');
  const isCommodity = sym.includes('GOLD') || sym.includes('CRUDE') || sym.includes('SILVER');
  const isCrypto = isCryptoAsset(symbol);

  if (isOption) {
    if (isIndex) return 'Index Options';
    if (isCommodity) return 'Gold/Crude Options';
    return 'Stock Options';
  } else {
    if (isCrypto) return 'Crypto Futures';
    if (isIndex) return 'Index Futures';
    if (isCommodity) return 'Gold/Crude Futures';
    return 'Stock Futures';
  }
};

const getSourceGroup = (item) => {
  if (item.real_or_paper === 'LIVE') return 'Live Broker Trades';
  if (item.signal_id !== null && item.signal_id !== undefined) return 'Manual Paper Trades';
  return 'Auto Paper Trades';
};

const calculatePnLSum = (items) => {
  let inrSum = 0;
  let usdSum = 0;
  items.forEach(it => {
    if (isCryptoAsset(it.symbol)) {
      usdSum += it.pnl || 0;
    } else {
      inrSum += it.pnl || 0;
    }
  });
  return { inr: inrSum, usd: usdSum };
};

const REPORT_CATEGORY_OPTIONS = [
  { id: 'INDEX_OPTIONS', label: 'Index Options', accent: '#3b82f6' },
  { id: 'CRYPTO_FUTURES', label: 'Crypto Futures', accent: '#22c55e' },
  { id: 'MCX_GOLD', label: 'MCX Gold', accent: '#f59e0b' },
  { id: 'MCX_CRUDEOIL', label: 'MCX Crudeoil', accent: '#ef4444' },
  { id: 'INDEX_FUTURES', label: 'Index Futures', accent: '#06b6d4' },
  { id: 'OTHER_OPTIONS', label: 'Other Options', accent: '#a855f7' },
  { id: 'OTHER', label: 'Other', accent: '#94a3b8' },
];

const REPORT_RANGE_OPTIONS = [
  { id: 'MONTH', label: 'Month' },
  { id: 'SIX_MONTHS', label: '6M' },
  { id: 'YEAR', label: 'Year' },
  { id: 'CUSTOM', label: 'Custom' },
];

const REPORT_CATEGORY_ORDER = REPORT_CATEGORY_OPTIONS.map(item => item.label);
const REPORT_CATEGORY_BY_ID = REPORT_CATEGORY_OPTIONS.reduce((acc, item) => ({ ...acc, [item.id]: item }), {});

const isOptionContract = (symbol) => {
  const sym = (symbol || '').toUpperCase();
  return sym.endsWith(' CE') || sym.endsWith(' PE') || sym.includes(' CE') || sym.includes(' PE');
};

const getHistoryAssetLabel = (symbol) => {
  const sym = (symbol || '').toUpperCase();
  const base = getNormalizedBaseSymbol(symbol);
  if (sym.includes('BTC')) return 'BTC';
  if (sym.includes('ETH')) return 'ETH';
  if (sym.includes('SOL')) return 'SOL';
  if (base === 'BANKNIFTY') return 'BNF';
  if (base === 'NIFTY') return 'Nifty';
  if (base === 'SENSEX') return 'Sensex';
  if (base === 'GOLD') return 'Gold';
  if (base === 'CRUDEOIL') return 'Crudeoil';
  return base || 'Other';
};

const getReportingCategoryInfo = (item) => {
  if (item.report_category_code && REPORT_CATEGORY_BY_ID[item.report_category_code]) {
    return REPORT_CATEGORY_BY_ID[item.report_category_code];
  }
  const symbol = item.symbol || '';
  const base = getNormalizedBaseSymbol(symbol);
  if (isCryptoAsset(symbol)) {
    return REPORT_CATEGORY_BY_ID.CRYPTO_FUTURES;
  }
  if (isOptionContract(symbol)) {
    if (['BANKNIFTY', 'NIFTY', 'SENSEX'].includes(base)) {
      return REPORT_CATEGORY_BY_ID.INDEX_OPTIONS;
    }
    return REPORT_CATEGORY_BY_ID.OTHER_OPTIONS;
  }
  if (base === 'GOLD') return REPORT_CATEGORY_BY_ID.MCX_GOLD;
  if (base === 'CRUDEOIL') return REPORT_CATEGORY_BY_ID.MCX_CRUDEOIL;
  if (['BANKNIFTY', 'NIFTY', 'SENSEX'].includes(base)) return REPORT_CATEGORY_BY_ID.INDEX_FUTURES;
  return REPORT_CATEGORY_BY_ID.OTHER;
};

const getStrategyName = (item) => {
  const rawStrategy = (item.strategy_name || item.source_name || '').trim();
  if (rawStrategy && rawStrategy.toLowerCase() !== 'webhook strategy alert') {
    return rawStrategy;
  }
  const category = getReportingCategoryInfo(item);
  const asset = getHistoryAssetLabel(item.symbol);
  if (category.id === 'INDEX_OPTIONS') return `${asset} Option Buying`;
  if (category.id === 'CRYPTO_FUTURES') return `${asset} Crypto Futures`;
  if (category.id === 'MCX_GOLD') return 'Gold';
  if (category.id === 'MCX_CRUDEOIL') return 'Crudeoil';
  if (category.id === 'INDEX_FUTURES') return `${asset} Futures`;
  return asset || 'Other Strategy';
};

const pad2 = (value) => String(value).padStart(2, '0');

const toDateKey = (isoString) => {
  if (!isoString) return 'Unknown Date';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return 'Unknown Date';
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
};

const formatDateKey = (dateKey) => {
  if (!dateKey || dateKey === 'Unknown Date') return dateKey || 'Unknown Date';
  const [year, month, day] = dateKey.split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${day}-${months[Number(month) - 1]}-${year}`;
};

const parseDateInput = (value, endOfDay = false) => {
  if (!value || !value.trim()) return null;
  const match = value.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  const [, year, month, day] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day), endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, endOfDay ? 999 : 0);
  return isNaN(date.getTime()) ? null : date;
};

const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const endOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);
const addDays = (date, days) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);

const getPositionDate = (item) => {
  const raw = item.exit_time || item.entry_time;
  const date = raw ? new Date(raw) : null;
  return date && !isNaN(date.getTime()) ? date : null;
};

const getReportRangeBounds = (rangeId, customStart, customEnd, items) => {
  const now = new Date();
  let start = new Date(now.getFullYear(), now.getMonth(), 1);
  let end = endOfDay(now);

  if (rangeId === 'SIX_MONTHS') {
    start = new Date(now.getFullYear(), now.getMonth() - 5, 1);
  } else if (rangeId === 'YEAR') {
    start = new Date(now.getFullYear(), 0, 1);
  } else if (rangeId === 'CUSTOM') {
    const parsedStart = parseDateInput(customStart);
    const parsedEnd = parseDateInput(customEnd, true);
    const itemDates = items.map(getPositionDate).filter(Boolean).sort((a, b) => a - b);
    start = parsedStart || (itemDates[0] ? startOfDay(itemDates[0]) : start);
    end = parsedEnd || (itemDates[itemDates.length - 1] ? endOfDay(itemDates[itemDates.length - 1]) : end);
  }

  if (start > end) {
    return { start: end, end: start };
  }
  return { start, end };
};

const isWithinRange = (item, rangeBounds) => {
  const date = getPositionDate(item);
  if (!date) return false;
  return date >= rangeBounds.start && date <= rangeBounds.end;
};

const getSortedKeys = (obj, preferredOrder = []) => {
  return Object.keys(obj).sort((a, b) => {
    const aIndex = preferredOrder.indexOf(a);
    const bIndex = preferredOrder.indexOf(b);
    if (aIndex !== -1 || bIndex !== -1) {
      if (aIndex === -1) return 1;
      if (bIndex === -1) return -1;
      return aIndex - bIndex;
    }
    return a.localeCompare(b);
  });
};

const getHistoryItems = (node) => {
  const items = [];
  Object.values(node || {}).forEach(value => {
    if (Array.isArray(value)) {
      items.push(...value);
    } else if (value && typeof value === 'object') {
      items.push(...getHistoryItems(value));
    }
  });
  return items;
};

const getGroupedHistory = (historyPositions) => {
  const grouped = {};
  historyPositions.forEach(item => {
    const category = getReportingCategoryInfo(item).label;
    const strategy = getStrategyName(item);
    const dateKey = toDateKey(item.exit_time || item.entry_time);

    if (!grouped[category]) {
      grouped[category] = {};
    }
    if (!grouped[category][strategy]) {
      grouped[category][strategy] = {};
    }
    if (!grouped[category][strategy][dateKey]) {
      grouped[category][strategy][dateKey] = [];
    }
    grouped[category][strategy][dateKey].push(item);
  });

  Object.values(grouped).forEach(strategyGroups => {
    Object.values(strategyGroups).forEach(dateGroups => {
      Object.values(dateGroups).forEach(items => {
        items.sort((a, b) => {
          const aTime = new Date(a.exit_time || a.entry_time || 0).getTime();
          const bTime = new Date(b.exit_time || b.entry_time || 0).getTime();
          return bTime - aTime;
        });
      });
    });
  });
  return grouped;
};

export default function PaperTradeScreen({ session, purgeTrigger }) {
  const { width, height } = useWindowDimensions();
  const isCompactLandscape = width > height && width < 900;
  const isNarrow = width < 430;
  const [positions, setPositions] = useState([]);
  const [stats, setStats] = useState({ total_pnl: 0, total_pnl_inr: 0, total_pnl_usd: 0, win_rate: 0, total_trades: 0 });
  const [mutedSymbols, setMutedSymbols] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('active');
  const [searchText, setSearchText] = useState('');
  const [selectedSourceFilter, setSelectedSourceFilter] = useState('ALL');
  const [reportRange, setReportRange] = useState('MONTH');
  const [reportStartDate, setReportStartDate] = useState('');
  const [reportEndDate, setReportEndDate] = useState('');
  const [selectedReportCategories, setSelectedReportCategories] = useState(REPORT_CATEGORY_OPTIONS.map(item => item.id));

  const [collapsedDates, setCollapsedDates] = useState({});
  const [collapsedSources, setCollapsedSources] = useState({});
  const [collapsedSubgroups, setCollapsedSubgroups] = useState({});

  const filteredPositions = positions.filter(pos => {
    // 1. Symbol Search Filter (matches symbol name case-insensitively)
    if (searchText.trim() !== '') {
      const q = searchText.toLowerCase().trim();
      const sym = (pos.symbol || '').toLowerCase();
      if (!sym.includes(q)) return false;
    }

    // 2. Source Filter (applied on history tab)
    if (activeTab === 'history') {
      const srcGroup = getSourceGroup(pos);
      if (selectedSourceFilter === 'AUTO' && srcGroup !== 'Auto Paper Trades') return false;
      if (selectedSourceFilter === 'MANUAL' && srcGroup !== 'Manual Paper Trades') return false;
      if (selectedSourceFilter === 'LIVE' && srcGroup !== 'Live Broker Trades') return false;
    }

    // 3. Tab Filter (Active vs History)
    if (activeTab === 'active') {
      if (['OPEN', 'PENDING', 'PARTIAL', 'EXIT_PENDING', 'EXIT_PARTIAL'].includes(pos.status)) return true;
      if (pos.status === 'CLOSED' && pos.exit_time) {
        const exitDate = new Date(pos.exit_time);
        const now = new Date();
        const diffMs = now - exitDate;
        const diffMins = diffMs / (1000 * 60);
        return diffMins <= 30; // Kept in Active for 30 minutes after exit
      }
      return false;
    } else {
      return ['CLOSED', 'REJECTED'].includes(pos.status);
    }
  });

  const filteredClosedPositions = filteredPositions.filter(p => p.status === 'CLOSED');
  const filteredPnL = calculatePnLSum(filteredClosedPositions);
  const reportRangeBounds = getReportRangeBounds(reportRange, reportStartDate, reportEndDate, filteredClosedPositions);
  const reportPositions = filteredClosedPositions.filter(item => {
    const category = getReportingCategoryInfo(item);
    return selectedReportCategories.includes(category.id) && isWithinRange(item, reportRangeBounds);
  });
  const reportPnL = calculatePnLSum(reportPositions);
  const reportTradingDays = Array.from(new Set(reportPositions.map(item => toDateKey(item.exit_time || item.entry_time)))).filter(key => key !== 'Unknown Date');
  const reportWinTrades = reportPositions.filter(item => (item.pnl || 0) > 0).length;
  const reportWinRate = reportPositions.length ? (reportWinTrades / reportPositions.length) * 100 : 0;
  const reportAvgInr = reportTradingDays.length ? reportPnL.inr / reportTradingDays.length : 0;
  const reportAvgUsd = reportTradingDays.length ? reportPnL.usd / reportTradingDays.length : 0;

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    setSearchText('');
    setSelectedSourceFilter('ALL');
  };

  const toggleReportCategory = (categoryId) => {
    setSelectedReportCategories(prev => {
      if (prev.includes(categoryId)) {
        return prev.length === 1 ? prev : prev.filter(id => id !== categoryId);
      }
      return [...prev, categoryId];
    });
  };

  const fetchPaperTrades = async () => {
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }

      const safeJson = async (res, label) => {
        if (!res.ok) { console.warn(`${label} returned HTTP ${res.status}`); return null; }
        const text = await res.text();
        try { return JSON.parse(text); } catch { console.warn(`${label} returned non-JSON`); return null; }
      };

      const response = await fetch(`${BACKEND_URL}/api/paper-trades`, { headers });
      const data = await safeJson(response, 'paper-trades');
      if (data) {
        setPositions(data.positions || []);
        setStats(data.stats || { total_pnl: 0, total_pnl_inr: 0, total_pnl_usd: 0, win_rate: 0, total_trades: 0 });
      }

      // Fetch settings for muted symbols
      const settingsRes = await fetch(`${BACKEND_URL}/api/user/settings`, { headers });
      const settingsData = await safeJson(settingsRes, 'user/settings');
      if (settingsData) setMutedSymbols(settingsData.muted_symbols || []);
    } catch (error) {
      console.warn("Error loading paper trades:", error.message);
    } finally {
      setLoading(false);
    }
  };

  const closePosition = async (id) => {
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      await fetch(`${BACKEND_URL}/api/broker/manual-exit/${id}`, {
        method: 'POST',
        headers
      });
      fetchPaperTrades();
    } catch (error) {
      console.error("Error closing trade:", error);
    }
  };

  const handleToggleMute = async (symbol) => {
    const sym = getNormalizedBaseSymbol(symbol);
    const isCurrentlyMuted = mutedSymbols.includes(sym);
    const newMuteStatus = !isCurrentlyMuted;

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }

      const response = await fetch(`${BACKEND_URL}/api/user/settings/mute`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ symbol: sym, mute: newMuteStatus })
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (response.ok && data) {
        setMutedSymbols(data.muted_symbols || []);
      } else {
        alert(`Error toggling mute: ${data?.detail || 'Request failed'}`);
      }
    } catch (error) {
      alert(`Network error toggling mute: ${error.message}`);
    }
  };

  useEffect(() => {
    fetchPaperTrades();
    const timer = setInterval(fetchPaperTrades, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (purgeTrigger > 0) {
      setPositions([]);
      setStats({ total_pnl: 0, total_pnl_inr: 0, total_pnl_usd: 0, win_rate: 0, total_trades: 0 });
      fetchPaperTrades();
    }
  }, [purgeTrigger]);

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  // Pre-process grouping and combined P&Ls (for active tab)
  const groupsData = {
    'Auto Paper Trades': { label: '🤖 Auto Paper Trades', accent: '#06b6d4', items: {} },
    'Manual Paper Trades': { label: '👆 Manual Paper Trades', accent: '#f97316', items: {} },
    'Live Broker Trades': { label: '⚡ Live Broker Trades', accent: '#a855f7', items: {} }
  };

  const groupPnLs = {
    'Auto Paper Trades': { inr: 0, usd: 0 },
    'Manual Paper Trades': { inr: 0, usd: 0 },
    'Live Broker Trades': { inr: 0, usd: 0 }
  };

  filteredPositions.forEach(item => {
    const src = getSourceGroup(item);
    const sub = getSubgroup(item.symbol);

    if (groupsData[src]) {
      if (!groupsData[src].items[sub]) {
        groupsData[src].items[sub] = [];
      }
      groupsData[src].items[sub].push(item);

      const isUSD = isCryptoAsset(item.symbol);
      if (isUSD) {
        groupPnLs[src].usd += item.pnl || 0;
      } else {
        groupPnLs[src].inr += item.pnl || 0;
      }
    }
  });

  const inrPnl = stats.total_pnl_inr || 0;
  const usdPnl = stats.total_pnl_usd || 0;
  const inrPnlStyle = inrPnl > 0 ? styles.textProfit : (inrPnl < 0 ? styles.textLoss : styles.textNeutral);
  const usdPnlStyle = usdPnl > 0 ? styles.textProfit : (usdPnl < 0 ? styles.textLoss : styles.textNeutral);

  const renderPositionCard = (item) => {
    const isClosed = item.status === 'CLOSED';
    const isEntryPending = item.status === 'PENDING';
    const isEntryPartial = item.status === 'PARTIAL';
    const isExitPending = item.status === 'EXIT_PENDING';
    const isExitPartial = item.status === 'EXIT_PARTIAL';
    const isItemProfit = item.pnl > 0;
    const isItemLoss = item.pnl < 0;
    const pnlTextColor = isItemProfit ? styles.textProfit : (isItemLoss ? styles.textLoss : styles.textNeutral);

    const displayDirection = item.direction;

    const isLong = displayDirection === 'LONG';
    const isUSD = isCryptoAsset(item.symbol);
    const currencySymbol = isUSD ? '$' : '₹';
    const locale = isUSD ? 'en-US' : 'en-IN';

    const lotSizeVal = getLotSize(item.symbol);
    const lotsCount = item.lot_size || Math.round(item.qty / lotSizeVal) || 1;
    const qtyDisplay = isUSD ? `${item.qty} Qty` : `${lotsCount} Lots (${item.qty} Qty)`;

    const baseSymbol = getNormalizedBaseSymbol(item.symbol);
    const isMuted = mutedSymbols.includes(baseSymbol);
    const exitReasonLabels = {
      INTRADAY_CUTOFF: '3:15 PM intraday close',
      TARGET_HIT: 'Target hit',
      SIGNAL_EXIT: 'Exit signal',
      MANUAL_EXIT: 'Manual exit',
    };
    const exitReason = exitReasonLabels[item.exit_reason] || item.exit_reason;

    return (
      <View key={item.id} style={[styles.card, isMuted && { opacity: 0.6 }]}>
        <View style={styles.cardHeader}>
          <View style={styles.positionTitleRow}>
            <View style={[styles.dirBadge, isLong ? styles.dirLong : styles.dirShort]}>
              <Text style={styles.dirText}>{displayDirection}</Text>
            </View>
            <Text style={styles.symbol} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.72}>
              {item.symbol}
            </Text>
          </View>
          <Text style={[styles.pnl, pnlTextColor]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.72}>
            {isClosed
              ? `${currencySymbol}${item.pnl.toLocaleString(locale, {minimumFractionDigits: 2})}`
              : isEntryPending
                ? 'ENTRY PENDING'
                : isEntryPartial
                  ? 'PARTIAL FILL'
                : isExitPending
                  ? 'EXIT PENDING'
                  : isExitPartial
                    ? 'PARTIAL EXIT'
                  : `${currencySymbol}${item.pnl.toLocaleString(locale, {minimumFractionDigits: 2})} (OPEN)`
            }
          </Text>
        </View>

        <View style={styles.positionMetaRow}>
          {item.timeframe && (
            <View style={styles.timeframeBadge}>
              <Text style={styles.timeframeBadgeText}>⏱ {item.timeframe}</Text>
            </View>
          )}
          {item.trade_type && (
            <View style={[
              styles.timeframeBadge,
              {
                backgroundColor: item.trade_type === 'POSITIONAL' ? 'rgba(59, 130, 246, 0.12)' : 'rgba(16, 185, 129, 0.12)',
                borderColor: item.trade_type === 'POSITIONAL' ? '#3b82f6' : '#10b981',
                borderWidth: 1,
              }
            ]}>
              <Text style={[styles.timeframeBadgeText, { color: item.trade_type === 'POSITIONAL' ? '#3b82f6' : '#10b981' }]}>
                {item.trade_type}
              </Text>
            </View>
          )}
          <TouchableOpacity onPress={() => handleToggleMute(item.symbol)} style={styles.muteButton}>
            <Text style={{ fontSize: 12 }}>{isMuted ? '🔕' : '🔔'}</Text>
          </TouchableOpacity>
          <Text style={styles.qty} numberOfLines={1}>{qtyDisplay}</Text>
        </View>

        <View style={[styles.cardDetails, isNarrow && styles.cardDetailsNarrow]}>
          <View style={styles.detailBlock}>
            <Text style={styles.detailText}>
              Entry: {currencySymbol}{item.entry_price.toLocaleString(locale, {minimumFractionDigits: 2})}
              {item.entry_time && ` (${formatSignalDate(item.entry_time)})`}
              {!isClosed && item.current_price && ` | LTP: ${currencySymbol}${item.current_price.toLocaleString(locale, {minimumFractionDigits: 2})}`}
            </Text>
            {isClosed && (
              <Text style={styles.detailText}>
                Exit: {currencySymbol}{item.exit_price.toLocaleString(locale, {minimumFractionDigits: 2})}
                {item.exit_time && ` (${formatSignalDate(item.exit_time)})`}
              </Text>
            )}
            {isClosed && exitReason && <Text style={styles.exitReason}>Closed: {exitReason}</Text>}
          </View>
          {!isClosed && item.status === 'OPEN' ? (
            <TouchableOpacity style={[styles.exitBtn, isNarrow && styles.exitBtnNarrow]} onPress={() => closePosition(item.id)}>
              <Text style={styles.exitBtnText}>MANUAL EXIT</Text>
            </TouchableOpacity>
          ) : !isClosed ? (
            <Text style={styles.detailText}>{item.order_status || 'AWAITING BROKER UPDATE'}</Text>
          ) : null}
        </View>
      </View>
    );
  };

  const renderSourceCardActive = (groupKey) => {
    const group = groupsData[groupKey];
    const pnlData = groupPnLs[groupKey];
    const subKeys = Object.keys(group.items);

    const hasTrades = subKeys.length > 0;
    const isProfitInr = pnlData.inr > 0;
    const isLossInr = pnlData.inr < 0;
    const isProfitUsd = pnlData.usd > 0;
    const isLossUsd = pnlData.usd < 0;

    const isSourceCollapsed = !!collapsedSources[`active_${groupKey}`];

    return (
      <View key={groupKey} style={[styles.sourceCard, { borderColor: group.accent + '25' }]}>
        {/* Source Header Banner */}
        <TouchableOpacity
          style={[styles.sourceHeader, { backgroundColor: group.accent + '0c', borderBottomColor: group.accent + '1a' }]}
          onPress={() => setCollapsedSources(prev => ({ ...prev, [`active_${groupKey}`]: !prev[`active_${groupKey}`] }))}
        >
          <View style={styles.sourceTitleBlock}>
            <Text style={{ fontSize: 12, color: '#9ca3af', marginRight: 6 }}>
              {isSourceCollapsed ? '▶' : '▼'}
            </Text>
            <View style={[styles.dotIndicator, { backgroundColor: group.accent }]} />
            <Text style={styles.sourceLabel}>{group.label}</Text>
          </View>
          <View style={styles.groupPnLBlock}>
            {pnlData.inr !== 0 && (
              <Text style={[styles.groupPnLText, isProfitInr ? styles.textProfit : (isLossInr ? styles.textLoss : styles.textNeutral)]}>
                {isProfitInr ? '+' : ''}₹{pnlData.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
              </Text>
            )}
            {pnlData.usd !== 0 && (
              <Text style={[styles.groupPnLText, isProfitUsd ? styles.textProfit : (isLossUsd ? styles.textLoss : styles.textNeutral)]}>
                {isProfitUsd ? '+' : ''}${pnlData.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}
              </Text>
            )}
            {!hasTrades && <Text style={styles.neutralBadge}>FLAT</Text>}
          </View>
        </TouchableOpacity>

        {/* Dynamic Nested Subgroups */}
        {!isSourceCollapsed && (
          <View style={styles.sourceContent}>
            {hasTrades ? (
              subKeys.map(subKey => {
                const items = group.items[subKey];
                let subInr = 0;
                let subUsd = 0;
                items.forEach(it => {
                  if (isCryptoAsset(it.symbol)) {
                    subUsd += it.pnl || 0;
                  } else {
                    subInr += it.pnl || 0;
                  }
                });

                const isSubProfitInr = subInr > 0;
                const isSubLossInr = subInr < 0;
                const isSubProfitUsd = subUsd > 0;
                const isSubLossUsd = subUsd < 0;

                const isSubCollapsed = !!collapsedSubgroups[`active_${groupKey}_${subKey}`];

                return (
                  <View key={subKey} style={styles.subgroupWrapper}>
                    {/* Subgroup Label Tag */}
                    <TouchableOpacity
                      style={styles.subgroupCollapseHeader}
                      onPress={() => setCollapsedSubgroups(prev => ({ ...prev, [`active_${groupKey}_${subKey}`]: !prev[`active_${groupKey}_${subKey}`] }))}
                    >
                      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                        <Text style={{ fontSize: 9, color: '#6b7280', marginRight: 6 }}>
                          {isSubCollapsed ? '▶' : '▼'}
                        </Text>
                        <Text style={styles.subgroupCollapseTitle}>{subKey}</Text>
                      </View>
                      <View style={styles.subgroupPnLBlock}>
                        {subInr !== 0 && (
                          <Text style={[styles.subgroupPnL, isSubProfitInr ? styles.textProfit : (isSubLossInr ? styles.textLoss : styles.textNeutral)]}>
                            {isSubProfitInr ? '+' : ''}₹{subInr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                          </Text>
                        )}
                        {subUsd !== 0 && (
                          <Text style={[styles.subgroupPnL, isSubProfitUsd ? styles.textProfit : (isSubLossUsd ? styles.textLoss : styles.textNeutral)]}>
                            {isSubProfitUsd ? '+' : ''}${subUsd.toLocaleString('en-US', {minimumFractionDigits: 2})}
                          </Text>
                        )}
                      </View>
                    </TouchableOpacity>

                    {/* Subgroup Positions List */}
                    {!isSubCollapsed && (
                      <View style={styles.subgroupCollapseContent}>
                        {items.map(item => renderPositionCard(item))}
                      </View>
                    )}
                  </View>
                );
              })
            ) : (
              <View style={styles.emptyGroupContent}>
                <Text style={styles.emptyGroupText}>No active contracts.</Text>
              </View>
            )}
          </View>
        )}
      </View>
    );
  };

  const renderHistoryPnL = (pnlData, textStyle = styles.subgroupPnL) => (
    <View style={{ flexDirection: 'row', alignItems: 'center' }}>
      {pnlData.inr === 0 && pnlData.usd === 0 && (
        <Text style={[textStyle, styles.textNeutral]}>₹0.00</Text>
      )}
      {pnlData.inr !== 0 && (
        <Text style={[textStyle, pnlData.inr > 0 ? styles.textProfit : (pnlData.inr < 0 ? styles.textLoss : styles.textNeutral)]}>
          {`${pnlData.inr > 0 ? '+' : ''}\u20b9${pnlData.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}`}
        </Text>
      )}
      {pnlData.usd !== 0 && (
        <Text style={[textStyle, pnlData.usd > 0 ? styles.textProfit : (pnlData.usd < 0 ? styles.textLoss : styles.textNeutral)]}>
          {`${pnlData.usd > 0 ? '+' : ''}$${pnlData.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}`}
        </Text>
      )}
    </View>
  );

  const renderReportControls = () => (
    <View style={styles.reportControls}>
      <View style={styles.filterButtonsRow}>
        {REPORT_RANGE_OPTIONS.map(option => (
          <TouchableOpacity
            key={option.id}
            style={[styles.filterBtn, reportRange === option.id && styles.filterBtnActive]}
            onPress={() => setReportRange(option.id)}
          >
            <Text style={[styles.filterBtnText, reportRange === option.id && styles.filterBtnTextActive]}>{option.label}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {reportRange === 'CUSTOM' && (
        <View style={styles.dateInputRow}>
          <TextInput
            style={[styles.searchInput, styles.dateInput]}
            placeholder="Start YYYY-MM-DD"
            placeholderTextColor="#6b7280"
            value={reportStartDate}
            onChangeText={setReportStartDate}
            keyboardType="numbers-and-punctuation"
          />
          <TextInput
            style={[styles.searchInput, styles.dateInput]}
            placeholder="End YYYY-MM-DD"
            placeholderTextColor="#6b7280"
            value={reportEndDate}
            onChangeText={setReportEndDate}
            keyboardType="numbers-and-punctuation"
          />
        </View>
      )}

      <View style={styles.categoryChipRow}>
        {REPORT_CATEGORY_OPTIONS.map(category => {
          const isSelected = selectedReportCategories.includes(category.id);
          return (
            <TouchableOpacity
              key={category.id}
              style={[
                styles.categoryChip,
                isSelected && { backgroundColor: category.accent + '22', borderColor: category.accent },
              ]}
              onPress={() => toggleReportCategory(category.id)}
            >
              <Text style={[styles.categoryChipText, isSelected && { color: '#ffffff' }]} numberOfLines={1}>
                {category.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );

  const renderReportSummary = () => (
    <View style={styles.reportSummaryCard}>
      <View style={styles.reportSummaryItem}>
        <Text style={styles.reportSummaryLabel}>TOTAL</Text>
        {renderHistoryPnL(reportPnL, styles.reportSummaryValue)}
      </View>
      <View style={styles.reportSummaryItem}>
        <Text style={styles.reportSummaryLabel}>AVG DAY</Text>
        {renderHistoryPnL({ inr: reportAvgInr, usd: reportAvgUsd }, styles.reportSummaryValue)}
      </View>
      <View style={styles.reportSummaryItem}>
        <Text style={styles.reportSummaryLabel}>WIN RATE</Text>
        <Text style={styles.reportSummaryValue}>{reportWinRate.toFixed(1)}%</Text>
      </View>
      <View style={styles.reportSummaryItem}>
        <Text style={styles.reportSummaryLabel}>TRADES</Text>
        <Text style={styles.reportSummaryValue}>{reportPositions.length}</Text>
      </View>
    </View>
  );

  const renderHistoryByStrategyDate = (items = filteredPositions, includePositionCards = true) => {
    const groupedHistory = getGroupedHistory(items);
    const sortedCategories = getSortedKeys(groupedHistory, REPORT_CATEGORY_ORDER);

    return sortedCategories.map(category => {
      const categoryMeta = REPORT_CATEGORY_OPTIONS.find(item => item.label === category) || REPORT_CATEGORY_BY_ID.OTHER;
      const categoryKey = `history_${category}`;
      const isCategoryCollapsed = !!collapsedSources[categoryKey];
      const categoryItems = getHistoryItems(groupedHistory[category]);
      const categoryPnL = calculatePnLSum(categoryItems);

      return (
        <View key={category} style={[styles.dateBlock, { borderColor: categoryMeta.accent + '30' }]}>
          <TouchableOpacity
            style={[styles.dateHeader, { backgroundColor: categoryMeta.accent + '0f', borderBottomColor: categoryMeta.accent + '22' }]}
            onPress={() => setCollapsedSources(prev => ({ ...prev, [categoryKey]: !prev[categoryKey] }))}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, minWidth: 0 }}>
              <Text style={{ fontSize: 13, color: categoryMeta.accent, fontWeight: 'bold', marginRight: 8 }}>
                {isCategoryCollapsed ? '>' : 'v'}
              </Text>
              <View style={[styles.dotIndicator, { backgroundColor: categoryMeta.accent }]} />
              <Text style={styles.dateTitle} numberOfLines={1}>{category}</Text>
            </View>
            {renderHistoryPnL(categoryPnL, styles.groupPnLText)}
          </TouchableOpacity>

          {!isCategoryCollapsed && (
            <View style={styles.dateContent}>
              {getSortedKeys(groupedHistory[category]).map(strategy => {
                const strategyKey = `history_${category}_${strategy}`;
                const isStrategyCollapsed = !!collapsedSubgroups[strategyKey];
                const strategyItems = getHistoryItems(groupedHistory[category][strategy]);
                const strategyPnL = calculatePnLSum(strategyItems);

                return (
                  <View key={strategy} style={styles.sourceWrapper}>
                    <TouchableOpacity
                      style={styles.sourceCollapseHeader}
                      onPress={() => setCollapsedSubgroups(prev => ({ ...prev, [strategyKey]: !prev[strategyKey] }))}
                    >
                      <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, minWidth: 0 }}>
                        <Text style={{ fontSize: 11, color: '#9ca3af', marginRight: 6 }}>
                          {isStrategyCollapsed ? '>' : 'v'}
                        </Text>
                        <Text style={styles.sourceCollapseTitle} numberOfLines={1}>{strategy}</Text>
                      </View>
                      {renderHistoryPnL(strategyPnL)}
                    </TouchableOpacity>

                    {!isStrategyCollapsed && (
                      <View style={styles.sourceCollapseContent}>
                        {getSortedKeys(groupedHistory[category][strategy]).sort((a, b) => b.localeCompare(a)).map(dateKey => {
                          const dateCollapseKey = `history_${category}_${strategy}_${dateKey}`;
                          const isDateCollapsed = !!collapsedDates[dateCollapseKey];
                          const dateItems = groupedHistory[category][strategy][dateKey];
                          const datePnL = calculatePnLSum(dateItems);

                          return (
                            <View key={dateKey} style={styles.subgroupWrapper}>
                              <TouchableOpacity
                                style={styles.subgroupCollapseHeader}
                                onPress={() => setCollapsedDates(prev => ({ ...prev, [dateCollapseKey]: !prev[dateCollapseKey] }))}
                              >
                                <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, minWidth: 0 }}>
                                  <Text style={{ fontSize: 9, color: '#6b7280', marginRight: 6 }}>
                                    {isDateCollapsed ? '>' : 'v'}
                                  </Text>
                                  <Text style={styles.subgroupCollapseTitle} numberOfLines={1}>{formatDateKey(dateKey)}</Text>
                                </View>
                                {renderHistoryPnL(datePnL)}
                              </TouchableOpacity>

                              {!isDateCollapsed && includePositionCards && (
                                <View style={styles.subgroupCollapseContent}>
                                  {dateItems.map(item => renderPositionCard(item))}
                                </View>
                              )}
                            </View>
                          );
                        })}
                      </View>
                    )}
                  </View>
                );
              })}
            </View>
          )}
        </View>
      );
    });
  };

  const renderReports = () => (
    <View>
      {renderReportSummary()}
      {reportPositions.length > 0 ? renderHistoryByStrategyDate(reportPositions, true) : (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyText}>No report data.</Text>
          <Text style={styles.emptySubtext}>Change the date or section filters to generate a report.</Text>
        </View>
      )}
    </View>
  );

  const renderDashboard = () => {
    const dayTotals = {};
    reportPositions.forEach(item => {
      const dateKey = toDateKey(item.exit_time || item.entry_time);
      if (dateKey === 'Unknown Date') return;
      if (!dayTotals[dateKey]) dayTotals[dateKey] = { inr: 0, usd: 0 };
      if (isCryptoAsset(item.symbol)) {
        dayTotals[dateKey].usd += item.pnl || 0;
      } else {
        dayTotals[dateKey].inr += item.pnl || 0;
      }
    });

    const dateSeries = [];
    let cursor = startOfDay(reportRangeBounds.start);
    const finalDate = startOfDay(reportRangeBounds.end);
    let guard = 0;
    while (cursor <= finalDate && guard < 370) {
      dateSeries.push(new Date(cursor));
      cursor = addDays(cursor, 1);
      guard += 1;
    }

    const firstDayOffset = dateSeries.length ? dateSeries[0].getDay() : 0;
    const paddedDays = [...Array(firstDayOffset).fill(null), ...dateSeries];

    return (
      <View>
        {renderReportSummary()}
        <View style={styles.calendarGrid}>
          {paddedDays.map((date, index) => {
            if (!date) {
              return <View key={`blank_${index}`} style={styles.calendarTileBlank} />;
            }
            const dateKey = toDateKey(date.toISOString());
            const pnl = dayTotals[dateKey] || { inr: 0, usd: 0 };
            const displayPnl = pnl.usd !== 0 ? pnl.usd : pnl.inr;
            const isProfit = displayPnl > 0;
            const isLoss = displayPnl < 0;
            const currency = pnl.usd !== 0 ? '$' : '₹';
            return (
              <View key={dateKey} style={[styles.calendarTile, isProfit && styles.calendarTileProfit, isLoss && styles.calendarTileLoss]}>
                <Text style={styles.calendarDay}>{date.getDate()}</Text>
                <Text
                  style={[styles.calendarPnl, isProfit ? styles.textProfit : (isLoss ? styles.textLoss : styles.textNeutral)]}
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.65}
                >
                  {displayPnl === 0 ? '-' : `${displayPnl > 0 ? '+' : ''}${currency}${Math.abs(displayPnl).toLocaleString(pnl.usd !== 0 ? 'en-US' : 'en-IN', { maximumFractionDigits: 0 })}`}
                </Text>
              </View>
            );
          })}
        </View>
      </View>
    );
  };

  const renderLedgerContent = () => {
    if (activeTab === 'active') {
      return Object.keys(groupsData).map(groupKey => renderSourceCardActive(groupKey));
    }
    if (activeTab === 'history') {
      return renderHistoryByStrategyDate(filteredPositions, true);
    }
    if (activeTab === 'reports') {
      return renderReports();
    }
    return renderDashboard();
  };

  const hasLedgerData = activeTab === 'reports' || activeTab === 'dashboard'
    ? reportPositions.length > 0 || filteredClosedPositions.length > 0
    : filteredPositions.length > 0;

  const getEmptyTitle = () => {
    if (activeTab === 'active') return 'No active positions.';
    if (activeTab === 'history') return 'No trade history.';
    return 'No report data.';
  };

  const getEmptySubtext = () => {
    if (activeTab === 'active') return 'Sourced signals will execute simulated trades here.';
    if (activeTab === 'history') return 'Your closed positions will be archived here.';
    return 'Closed trades will appear here once they match the filters.';
  };

  return (
    <View style={[styles.container, isCompactLandscape && styles.containerCompact]}>
      {/* Top Combined Performance Stats Bar */}
      <View style={[styles.statsContainer, isCompactLandscape && styles.statsContainerCompact]}>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>INR P&L</Text>
          <Text style={[styles.statValue, inrPnlStyle]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65}>
            ₹{inrPnl.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>USD P&L</Text>
          <Text style={[styles.statValue, usdPnlStyle]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65}>
            ${usdPnl.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>WIN RATE</Text>
          <Text style={styles.statValue} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65}>{(stats.win_rate || 0).toFixed(1)}%</Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>TRADES</Text>
          <Text style={styles.statValue} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65}>{stats.total_trades}</Text>
        </View>
      </View>

      {/* Ledger Header Section */}
      <View style={styles.ledgerHeader}>
        <Text style={styles.title}>Positions Ledger</Text>
        <View style={styles.tabContainer}>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'active' && styles.tabButtonActive]}
            onPress={() => handleTabChange('active')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'active' && styles.tabButtonTextActive]}>Active</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'history' && styles.tabButtonActive]}
            onPress={() => handleTabChange('history')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'history' && styles.tabButtonTextActive]}>History</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'reports' && styles.tabButtonActive]}
            onPress={() => handleTabChange('reports')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'reports' && styles.tabButtonTextActive]}>Reports</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'dashboard' && styles.tabButtonActive]}
            onPress={() => handleTabChange('dashboard')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'dashboard' && styles.tabButtonTextActive]}>Dash</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Search and Filter Inputs */}
      {activeTab !== 'active' && (
        <View style={styles.filterBar}>
          <TextInput
            style={styles.searchInput}
            placeholder="Search symbol (e.g. NIFTY)..."
            placeholderTextColor="#6b7280"
            value={searchText}
            onChangeText={setSearchText}
          />
          {activeTab === 'history' ? (
            <View style={styles.filterButtonsRow}>
              <TouchableOpacity
                style={[styles.filterBtn, selectedSourceFilter === 'ALL' && styles.filterBtnActive]}
                onPress={() => setSelectedSourceFilter('ALL')}
              >
                <Text style={[styles.filterBtnText, selectedSourceFilter === 'ALL' && styles.filterBtnTextActive]}>ALL</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.filterBtn, selectedSourceFilter === 'AUTO' && styles.filterBtnActive]}
                onPress={() => setSelectedSourceFilter('AUTO')}
              >
                <Text style={[styles.filterBtnText, selectedSourceFilter === 'AUTO' && styles.filterBtnTextActive]}>AUTO</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.filterBtn, selectedSourceFilter === 'MANUAL' && styles.filterBtnActive]}
                onPress={() => setSelectedSourceFilter('MANUAL')}
              >
                <Text style={[styles.filterBtnText, selectedSourceFilter === 'MANUAL' && styles.filterBtnTextActive]}>MANUAL</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.filterBtn, selectedSourceFilter === 'LIVE' && styles.filterBtnActive]}
                onPress={() => setSelectedSourceFilter('LIVE')}
              >
                <Text style={[styles.filterBtnText, selectedSourceFilter === 'LIVE' && styles.filterBtnTextActive]}>LIVE</Text>
              </TouchableOpacity>
            </View>
          ) : renderReportControls()}
          {/* Dynamic P&L Subtotals */}
          {(searchText.trim() !== '' || selectedSourceFilter !== 'ALL' || activeTab === 'reports' || activeTab === 'dashboard') && (
            <View style={styles.subtotalBanner}>
              <Text style={styles.subtotalLabel}>{activeTab === 'history' ? 'FILTERED SUB-TOTALS:' : 'REPORT TOTALS:'}</Text>
              <View style={{ flexDirection: 'row' }}>
                <Text style={[styles.subtotalValue, (activeTab === 'history' ? filteredPnL.inr : reportPnL.inr) >= 0 ? styles.textProfit : styles.textLoss]}>
                  {(activeTab === 'history' ? filteredPnL.inr : reportPnL.inr) >= 0 ? '+' : ''}₹{(activeTab === 'history' ? filteredPnL.inr : reportPnL.inr).toLocaleString('en-IN', {minimumFractionDigits: 2})}
                </Text>
                {(activeTab === 'history' ? filteredPnL.usd : reportPnL.usd) !== 0 && (
                  <Text style={[styles.subtotalValue, { marginLeft: 10 }, (activeTab === 'history' ? filteredPnL.usd : reportPnL.usd) >= 0 ? styles.textProfit : styles.textLoss]}>
                    {(activeTab === 'history' ? filteredPnL.usd : reportPnL.usd) >= 0 ? '+' : ''}${(activeTab === 'history' ? filteredPnL.usd : reportPnL.usd).toLocaleString('en-US', {minimumFractionDigits: 2})}
                  </Text>
                )}
              </View>
            </View>
          )}
        </View>
      )}

      {/* Main Grouped Ledger Scroll Board */}
      <ScrollView showsVerticalScrollIndicator={false} style={styles.scrollBoard}>
        {hasLedgerData ? (
          renderLedgerContent()
        ) : (
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>{getEmptyTitle()}</Text>
            <Text style={styles.emptySubtext}>{getEmptySubtext()}</Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#070a13',
    padding: 14,
  },
  containerCompact: {
    padding: 10,
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#070a13',
    justifyContent: 'center',
    alignItems: 'center',
  },
  statsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderColor: 'rgba(255, 255, 255, 0.06)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 16,
  },
  statsContainerCompact: {
    paddingVertical: 8,
    marginBottom: 10,
  },
  statBox: {
    alignItems: 'center',
    flex: 1,
  },
  statLabel: {
    fontSize: 9,
    fontWeight: 'bold',
    color: '#9ca3af',
    marginBottom: 4,
  },
  statValue: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#ffffff',
    width: '100%',
    textAlign: 'center',
  },
  title: {
    fontSize: 15,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  scrollBoard: {
    flex: 1,
  },
  sourceCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
    borderWidth: 1,
    borderRadius: 14,
    marginBottom: 16,
    overflow: 'hidden',
  },
  sourceHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
  },
  sourceTitleBlock: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dotIndicator: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 8,
  },
  sourceLabel: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  groupPnLBlock: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  groupPnLText: {
    fontSize: 11,
    fontWeight: 'bold',
    marginLeft: 8,
  },
  neutralBadge: {
    fontSize: 8,
    color: '#9ca3af',
    fontWeight: 'bold',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 4,
  },
  sourceContent: {
    padding: 10,
  },
  subgroupBlock: {
    marginBottom: 12,
  },
  subgroupHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
    paddingHorizontal: 4,
  },
  subgroupTitle: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#3b82f6',
    backgroundColor: 'rgba(59, 130, 246, 0.08)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  subgroupPnLBlock: {
    flexDirection: 'row',
  },
  subgroupPnL: {
    fontSize: 10,
    fontWeight: 'bold',
    marginLeft: 6,
  },
  card: {
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 10,
    padding: 12,
    marginBottom: 6,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  positionTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    minWidth: 0,
  },
  dirBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    marginRight: 6,
  },
  dirLong: {
    backgroundColor: 'rgba(16, 185, 129, 0.15)',
  },
  dirShort: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
  },
  dirText: {
    fontSize: 8,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  symbol: {
    fontSize: 13,
    fontWeight: 'bold',
    color: '#ffffff',
    flex: 1,
    minWidth: 0,
  },
  positionMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    marginBottom: 6,
  },
  muteButton: {
    paddingHorizontal: 4,
    marginRight: 2,
  },
  qty: {
    fontSize: 10,
    color: '#9ca3af',
    marginLeft: 'auto',
  },
  pnl: {
    fontSize: 13,
    fontWeight: 'bold',
    maxWidth: '42%',
    marginLeft: 8,
    textAlign: 'right',
  },
  textProfit: { color: '#10b981' },
  textLoss: { color: '#ef4444' },
  textNeutral: { color: '#ffffff' },
  cardDetails: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.03)',
    paddingTop: 6,
    marginTop: 2,
  },
  cardDetailsNarrow: {
    flexDirection: 'column',
    alignItems: 'stretch',
  },
  detailBlock: {
    flex: 1,
    minWidth: 0,
  },
  detailText: {
    fontSize: 10,
    color: '#9ca3af',
    lineHeight: 15,
    flexShrink: 1,
  },
  exitReason: {
    color: '#60a5fa',
    fontSize: 9,
    fontWeight: 'bold',
    marginTop: 3,
  },
  exitBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.12)',
    borderColor: 'rgba(239, 68, 68, 0.25)',
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    marginLeft: 8,
    flexShrink: 0,
  },
  exitBtnNarrow: {
    alignSelf: 'flex-end',
    marginLeft: 0,
    marginTop: 8,
  },
  exitBtnText: {
    color: '#ef4444',
    fontSize: 9,
    fontWeight: 'bold',
  },
  emptyGroupContent: {
    alignItems: 'center',
    paddingVertical: 12,
  },
  emptyGroupText: {
    fontSize: 10,
    color: '#4b5563',
    fontStyle: 'italic',
  },
  emptyContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 60,
  },
  emptyText: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#9ca3af',
    marginBottom: 4,
  },
  emptySubtext: {
    fontSize: 11,
    color: '#4b5563',
    textAlign: 'center',
  },
  ledgerHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  tabContainer: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.05)',
    padding: 2,
    flexShrink: 1,
  },
  tabButton: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  tabButtonActive: {
    backgroundColor: '#3b82f6',
  },
  tabButtonText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#9ca3af',
  },
  tabButtonTextActive: {
    color: '#ffffff',
  },

  // Date Group Styles
  dateBlock: {
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.04)',
    borderRadius: 14,
    marginBottom: 16,
    overflow: 'hidden',
  },
  dateHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 12,
    backgroundColor: 'rgba(59, 130, 246, 0.05)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(59, 130, 246, 0.1)',
  },
  dateTitle: {
    fontSize: 13,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  dateContent: {
    padding: 10,
  },
  sourceWrapper: {
    marginBottom: 10,
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.03)',
    overflow: 'hidden',
  },
  sourceCollapseHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255, 255, 255, 0.03)',
  },
  sourceCollapseTitle: {
    fontSize: 11,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  sourceCollapseContent: {
    padding: 8,
  },
  subgroupWrapper: {
    marginBottom: 8,
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.02)',
    overflow: 'hidden',
  },
  subgroupCollapseHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
  },
  subgroupCollapseTitle: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#3b82f6',
  },
  subgroupCollapseContent: {
    paddingHorizontal: 4,
    paddingVertical: 4,
  },
  timeframeBadge: {
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    borderColor: 'rgba(255, 255, 255, 0.12)',
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 1,
    marginRight: 4,
  },
  timeframeBadgeText: {
    fontSize: 8,
    fontWeight: 'bold',
    color: '#9ca3af',
  },
  reportControls: {
    marginTop: 2,
  },
  dateInputRow: {
    flexDirection: 'row',
    marginTop: 8,
  },
  dateInput: {
    flex: 1,
    marginBottom: 0,
    marginHorizontal: 2,
  },
  categoryChipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    marginTop: 8,
  },
  categoryChip: {
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 6,
    marginRight: 6,
    marginBottom: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    maxWidth: 150,
  },
  categoryChipText: {
    color: '#9ca3af',
    fontSize: 9,
    fontWeight: 'bold',
  },
  reportSummaryCard: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    backgroundColor: 'rgba(255, 255, 255, 0.025)',
    borderColor: 'rgba(255, 255, 255, 0.06)',
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    marginBottom: 12,
  },
  reportSummaryItem: {
    width: '50%',
    paddingVertical: 6,
    paddingHorizontal: 6,
  },
  reportSummaryLabel: {
    color: '#9ca3af',
    fontSize: 9,
    fontWeight: 'bold',
    marginBottom: 3,
  },
  reportSummaryValue: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: 'bold',
    marginRight: 8,
  },
  calendarGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    marginHorizontal: -2,
  },
  calendarTile: {
    width: '13.7%',
    minHeight: 48,
    marginHorizontal: 2,
    marginBottom: 4,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.06)',
    backgroundColor: 'rgba(255, 255, 255, 0.025)',
    padding: 5,
    justifyContent: 'space-between',
  },
  calendarTileBlank: {
    width: '13.7%',
    minHeight: 48,
    marginHorizontal: 2,
    marginBottom: 4,
  },
  calendarTileProfit: {
    backgroundColor: 'rgba(16, 185, 129, 0.08)',
    borderColor: 'rgba(16, 185, 129, 0.22)',
  },
  calendarTileLoss: {
    backgroundColor: 'rgba(239, 68, 68, 0.08)',
    borderColor: 'rgba(239, 68, 68, 0.24)',
  },
  calendarDay: {
    color: '#cbd5e1',
    fontSize: 10,
    fontWeight: 'bold',
  },
  calendarPnl: {
    fontSize: 9,
    fontWeight: 'bold',
  },
  filterBar: {
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderColor: 'rgba(255, 255, 255, 0.06)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    marginBottom: 12,
  },
  searchInput: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    color: '#ffffff',
    fontSize: 12,
    marginBottom: 8,
  },
  filterButtonsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  filterBtn: {
    flex: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderRadius: 6,
    paddingVertical: 6,
    alignItems: 'center',
    marginHorizontal: 2,
  },
  filterBtnActive: {
    backgroundColor: '#3b82f6',
    borderColor: '#3b82f6',
  },
  filterBtnText: {
    fontSize: 9,
    fontWeight: 'bold',
    color: '#9ca3af',
  },
  filterBtnTextActive: {
    color: '#ffffff',
  },
  subtotalBanner: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.06)',
    paddingTop: 8,
    marginTop: 8,
  },
  subtotalLabel: {
    fontSize: 9,
    fontWeight: 'bold',
    color: '#9ca3af',
  },
  subtotalValue: {
    fontSize: 11,
    fontWeight: 'bold',
  },
});

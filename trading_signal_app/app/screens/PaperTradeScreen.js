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
  return sym.includes('BTC') || sym.includes('ETH') || sym.includes('SOL') || sym.includes('USD') || sym.includes('USDT');
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

const getGroupedHistory = (closedPos) => {
  const grouped = {};
  closedPos.forEach(item => {
    const exitDateStr = item.exit_time ? item.exit_time.split('T')[0] : 'Unknown Date';
    const source = getSourceGroup(item);
    const sub = getSubgroup(item.symbol);
    
    if (!grouped[exitDateStr]) {
      grouped[exitDateStr] = {};
    }
    if (!grouped[exitDateStr][source]) {
      grouped[exitDateStr][source] = {};
    }
    if (!grouped[exitDateStr][source][sub]) {
      grouped[exitDateStr][source][sub] = [];
    }
    grouped[exitDateStr][source][sub].push(item);
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

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    setSearchText('');
    setSelectedSourceFilter('ALL');
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
    
    const symUpper = (item.symbol || '').toUpperCase();
    const isPE = symUpper.endsWith(' PE') || symUpper.includes(' PE') || symUpper.endsWith('PE');
    const isCE = symUpper.endsWith(' CE') || symUpper.includes(' CE') || symUpper.endsWith('CE');
    
    let displayDirection = item.direction;
    if (isPE) {
      displayDirection = 'SHORT';
    } else if (isCE) {
      displayDirection = 'LONG';
    }

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

  const renderHistoryCollapsible = () => {
    const groupedHistory = getGroupedHistory(filteredPositions);
    const sortedDates = Object.keys(groupedHistory).sort((a, b) => b.localeCompare(a));
    
    return sortedDates.map(date => {
      const isDateCollapsed = !!collapsedDates[date];
      const dateItems = [];
      Object.keys(groupedHistory[date]).forEach(src => {
        Object.keys(groupedHistory[date][src]).forEach(sub => {
          dateItems.push(...groupedHistory[date][src][sub]);
        });
      });
      const datePnL = calculatePnLSum(dateItems);
      
      return (
        <View key={date} style={styles.dateBlock}>
          {/* Date Header Banner */}
          <TouchableOpacity 
            style={styles.dateHeader} 
            onPress={() => setCollapsedDates(prev => ({ ...prev, [date]: !prev[date] }))}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <Text style={{ fontSize: 13, color: '#3b82f6', fontWeight: 'bold', marginRight: 8 }}>
                {isDateCollapsed ? '▶' : '▼'}
              </Text>
              <Text style={styles.dateTitle}>{date}</Text>
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              {datePnL.inr !== 0 && (
                <Text style={[styles.subgroupPnL, datePnL.inr > 0 ? styles.textProfit : (datePnL.inr < 0 ? styles.textLoss : styles.textNeutral)]}>
                  {datePnL.inr > 0 ? '+' : ''}₹{datePnL.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                </Text>
              )}
              {datePnL.usd !== 0 && (
                <Text style={[styles.subgroupPnL, datePnL.usd > 0 ? styles.textProfit : (datePnL.usd < 0 ? styles.textLoss : styles.textNeutral), { marginLeft: 8 }]}>
                  {datePnL.usd > 0 ? '+' : ''}${datePnL.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}
                </Text>
              )}
            </View>
          </TouchableOpacity>

          {!isDateCollapsed && (
            <View style={styles.dateContent}>
              {Object.keys(groupedHistory[date]).map(source => {
                const isSourceCollapsed = !!collapsedSources[`${date}_${source}`];
                const sourceItems = [];
                Object.keys(groupedHistory[date][source]).forEach(sub => {
                  sourceItems.push(...groupedHistory[date][source][sub]);
                });
                const sourcePnL = calculatePnLSum(sourceItems);
                
                return (
                  <View key={source} style={styles.sourceWrapper}>
                    <TouchableOpacity 
                      style={styles.sourceCollapseHeader}
                      onPress={() => setCollapsedSources(prev => ({ ...prev, [`${date}_${source}`]: !prev[`${date}_${source}`] }))}
                    >
                      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                        <Text style={{ fontSize: 11, color: '#9ca3af', marginRight: 6 }}>
                          {isSourceCollapsed ? '▶' : '▼'}
                        </Text>
                        <Text style={styles.sourceCollapseTitle}>{source}</Text>
                      </View>
                      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                        {sourcePnL.inr !== 0 && (
                          <Text style={[styles.subgroupPnL, sourcePnL.inr > 0 ? styles.textProfit : (sourcePnL.inr < 0 ? styles.textLoss : styles.textNeutral)]}>
                            {sourcePnL.inr > 0 ? '+' : ''}₹{sourcePnL.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                          </Text>
                        )}
                        {sourcePnL.usd !== 0 && (
                          <Text style={[styles.subgroupPnL, sourcePnL.usd > 0 ? styles.textProfit : (sourcePnL.usd < 0 ? styles.textLoss : styles.textNeutral), { marginLeft: 8 }]}>
                            {sourcePnL.usd > 0 ? '+' : ''}${sourcePnL.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}
                          </Text>
                        )}
                      </View>
                    </TouchableOpacity>

                    {!isSourceCollapsed && (
                      <View style={styles.sourceCollapseContent}>
                        {Object.keys(groupedHistory[date][source]).map(subgroup => {
                          const isSubCollapsed = !!collapsedSubgroups[`${date}_${source}_${subgroup}`];
                          const subgroupItems = groupedHistory[date][source][subgroup];
                          const subgroupPnL = calculatePnLSum(subgroupItems);
                          
                          return (
                            <View key={subgroup} style={styles.subgroupWrapper}>
                              <TouchableOpacity 
                                style={styles.subgroupCollapseHeader}
                                onPress={() => setCollapsedSubgroups(prev => ({ ...prev, [`${date}_${source}_${subgroup}`]: !prev[`${date}_${source}_${subgroup}`] }))}
                              >
                                <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                                  <Text style={{ fontSize: 9, color: '#6b7280', marginRight: 6 }}>
                                    {isSubCollapsed ? '▶' : '▼'}
                                  </Text>
                                  <Text style={styles.subgroupCollapseTitle}>{subgroup}</Text>
                                </View>
                                <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                                  {subgroupPnL.inr !== 0 && (
                                    <Text style={[styles.subgroupPnL, subgroupPnL.inr > 0 ? styles.textProfit : (subgroupPnL.inr < 0 ? styles.textLoss : styles.textNeutral)]}>
                                      {subgroupPnL.inr > 0 ? '+' : ''}₹{subgroupPnL.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                                    </Text>
                                  )}
                                  {subgroupPnL.usd !== 0 && (
                                    <Text style={[styles.subgroupPnL, subgroupPnL.usd > 0 ? styles.textProfit : (subgroupPnL.usd < 0 ? styles.textLoss : styles.textNeutral), { marginLeft: 8 }]}>
                                      {subgroupPnL.usd > 0 ? '+' : ''}${subgroupPnL.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}
                                    </Text>
                                  )}
                                </View>
                              </TouchableOpacity>

                              {!isSubCollapsed && (
                                <View style={styles.subgroupCollapseContent}>
                                  {subgroupItems.map(item => renderPositionCard(item))}
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
        </View>
      </View>

      {/* Search and Filter Inputs for History Tab */}
      {activeTab === 'history' && (
        <View style={styles.filterBar}>
          <TextInput
            style={styles.searchInput}
            placeholder="Search symbol (e.g. NIFTY)..."
            placeholderTextColor="#6b7280"
            value={searchText}
            onChangeText={setSearchText}
          />
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
          {/* Dynamic P&L Subtotals */}
          {(searchText.trim() !== '' || selectedSourceFilter !== 'ALL') && (
            <View style={styles.subtotalBanner}>
              <Text style={styles.subtotalLabel}>FILTERED SUB-TOTALS:</Text>
              <View style={{ flexDirection: 'row' }}>
                <Text style={[styles.subtotalValue, filteredPnL.inr >= 0 ? styles.textProfit : styles.textLoss]}>
                  {filteredPnL.inr >= 0 ? '+' : ''}₹{filteredPnL.inr.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                </Text>
                {filteredPnL.usd !== 0 && (
                  <Text style={[styles.subtotalValue, { marginLeft: 10 }, filteredPnL.usd >= 0 ? styles.textProfit : styles.textLoss]}>
                    {filteredPnL.usd >= 0 ? '+' : ''}${filteredPnL.usd.toLocaleString('en-US', {minimumFractionDigits: 2})}
                  </Text>
                )}
              </View>
            </View>
          )}
        </View>
      )}
      
      {/* Main Grouped Ledger Scroll Board */}
      <ScrollView showsVerticalScrollIndicator={false} style={styles.scrollBoard}>
        {filteredPositions.length > 0 ? (
          activeTab === 'active' 
            ? Object.keys(groupsData).map(groupKey => renderSourceCardActive(groupKey))
            : renderHistoryCollapsible()
        ) : (
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>
              {activeTab === 'active' ? "No active positions." : "No trade history."}
            </Text>
            <Text style={styles.emptySubtext}>
              {activeTab === 'active' 
                ? "Sourced signals will execute simulated trades here."
                : "Your closed positions will be archived here."
              }
            </Text>
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
    marginBottom: 10,
  },
  tabContainer: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.05)',
    padding: 2,
  },
  tabButton: {
    paddingHorizontal: 12,
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

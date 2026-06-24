import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, ScrollView, TouchableOpacity, ActivityIndicator } from 'react-native';
import { BACKEND_URL } from '../config';

export default function PaperTradeScreen({ session }) {
  const [positions, setPositions] = useState([]);
  const [stats, setStats] = useState({ total_pnl: 0, total_pnl_inr: 0, total_pnl_usd: 0, win_rate: 0, total_trades: 0 });
  const [mutedSymbols, setMutedSymbols] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('active');

  const [collapsedDates, setCollapsedDates] = useState({});
  const [collapsedSources, setCollapsedSources] = useState({});
  const [collapsedSubgroups, setCollapsedSubgroups] = useState({});

  const filteredPositions = positions.filter(pos => {
    if (activeTab === 'active') {
      if (pos.status === 'OPEN') return true;
      if (pos.status === 'CLOSED' && pos.exit_time) {
        const exitDate = new Date(pos.exit_time);
        const now = new Date();
        const diffMs = now - exitDate;
        const diffMins = diffMs / (1000 * 60);
        return diffMins <= 30; // Kept in Active for 30 minutes after exit
      }
      return false;
    } else {
      return pos.status === 'CLOSED';
    }
  });

  const fetchPaperTrades = async () => {
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      
      const response = await fetch(`${BACKEND_URL}/api/paper-trades`, { headers });
      const data = await response.json();
      setPositions(data.positions || []);
      setStats(data.stats || { total_pnl: 0, total_pnl_inr: 0, total_pnl_usd: 0, win_rate: 0, total_trades: 0 });

      // Fetch settings for muted symbols
      const settingsRes = await fetch(`${BACKEND_URL}/api/user/settings`, { headers });
      if (settingsRes.ok) {
        const settingsData = await settingsRes.json();
        setMutedSymbols(settingsData.muted_symbols || []);
      }
    } catch (error) {
      console.error("Error loading paper trades:", error);
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
      const data = await response.json();
      if (response.ok) {
        setMutedSymbols(data.muted_symbols || []);
      } else {
        alert(`Error toggling mute: ${data.detail || 'Request failed'}`);
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

  // Helper to categorize asset types
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

  // Helper to identify source types
  const getSourceGroup = (item) => {
    if (item.real_or_paper === 'LIVE') return 'Live Broker Trades';
    if (item.signal_id !== null && item.signal_id !== undefined) return 'Manual Paper Trades';
    return 'Auto Paper Trades';
  };

  // Helper to calculate P&Ls for a set of items
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

  // Group closed positions by date, then source, then subgroup
  const getGroupedHistory = (closedPos) => {
    const grouped = {}; // { 'YYYY-MM-DD': { 'Source Group': { 'Subgroup': [positions] } } }
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

    return (
      <View key={item.id} style={[styles.card, isMuted && { opacity: 0.6 }]}>
        <View style={styles.cardHeader}>
          <View style={styles.leftHeader}>
            <View style={[styles.dirBadge, isLong ? styles.dirLong : styles.dirShort]}>
              <Text style={styles.dirText}>{displayDirection}</Text>
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <Text style={styles.symbol}>{item.symbol}</Text>
              <TouchableOpacity onPress={() => handleToggleMute(item.symbol)} style={{ paddingHorizontal: 4 }}>
                <Text style={{ fontSize: 12 }}>{isMuted ? '🔕' : '🔔'}</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.qty}>{qtyDisplay}</Text>
          </View>
          <Text style={[styles.pnl, pnlTextColor]}>
            {isClosed 
              ? `${currencySymbol}${item.pnl.toLocaleString(locale, {minimumFractionDigits: 2})}` 
              : `${currencySymbol}${item.pnl.toLocaleString(locale, {minimumFractionDigits: 2})} (OPEN)`
            }
          </Text>
        </View>
        
        <View style={styles.cardDetails}>
          <Text style={styles.detailText}>
            Entry: {currencySymbol}{item.entry_price.toLocaleString(locale, {minimumFractionDigits: 2})}
            {!isClosed && item.current_price && ` | LTP: ${currencySymbol}${item.current_price.toLocaleString(locale, {minimumFractionDigits: 2})}`}
          </Text>
          {isClosed ? (
            <Text style={styles.detailText}>Exit: {currencySymbol}{item.exit_price.toLocaleString(locale, {minimumFractionDigits: 2})}</Text>
          ) : (
            <TouchableOpacity style={styles.exitBtn} onPress={() => closePosition(item.id)}>
              <Text style={styles.exitBtnText}>MANUAL EXIT</Text>
            </TouchableOpacity>
          )}
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

    return (
      <View key={groupKey} style={[styles.sourceCard, { borderColor: group.accent + '25' }]}>
        {/* Source Header Banner */}
        <View style={[styles.sourceHeader, { backgroundColor: group.accent + '0c', borderBottomColor: group.accent + '1a' }]}>
          <View style={styles.sourceTitleBlock}>
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
        </View>

        {/* Dynamic Nested Subgroups */}
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

              return (
                <View key={subKey} style={styles.subgroupBlock}>
                  {/* Subgroup Label Tag */}
                  <View style={styles.subgroupHeader}>
                    <Text style={styles.subgroupTitle}>{subKey}</Text>
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
                  </View>

                  {/* Subgroup Positions List */}
                  {items.map(item => renderPositionCard(item))}
                </View>
              );
            })
          ) : (
            <View style={styles.emptyGroupContent}>
              <Text style={styles.emptyGroupText}>No active contracts.</Text>
            </View>
          )}
        </View>
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
    <View style={styles.container}>
      {/* Top Combined Performance Stats Bar */}
      <View style={styles.statsContainer}>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>INR P&L</Text>
          <Text style={[styles.statValue, inrPnlStyle]}>
            ₹{inrPnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>USD P&L</Text>
          <Text style={[styles.statValue, usdPnlStyle]}>
            ${usdPnl.toLocaleString('en-US', {minimumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>WIN RATE</Text>
          <Text style={styles.statValue}>{(stats.win_rate || 0).toFixed(1)}%</Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>TRADES</Text>
          <Text style={styles.statValue}>{stats.total_trades}</Text>
        </View>
      </View>

      {/* Ledger Header Section */}
      <View style={styles.ledgerHeader}>
        <Text style={styles.title}>Positions Ledger</Text>
        <View style={styles.tabContainer}>
          <TouchableOpacity 
            style={[styles.tabButton, activeTab === 'active' && styles.tabButtonActive]} 
            onPress={() => setActiveTab('active')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'active' && styles.tabButtonTextActive]}>Active</Text>
          </TouchableOpacity>
          <TouchableOpacity 
            style={[styles.tabButton, activeTab === 'history' && styles.tabButtonActive]} 
            onPress={() => setActiveTab('history')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'history' && styles.tabButtonTextActive]}>History</Text>
          </TouchableOpacity>
        </View>
      </View>
      
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
  leftHeader: {
    flexDirection: 'row',
    alignItems: 'center',
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
    marginRight: 6,
  },
  qty: {
    fontSize: 10,
    color: '#9ca3af',
    marginLeft: 6,
  },
  pnl: {
    fontSize: 13,
    fontWeight: 'bold',
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
  detailText: {
    fontSize: 10,
    color: '#9ca3af',
  },
  exitBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.12)',
    borderColor: 'rgba(239, 68, 68, 0.25)',
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
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
});

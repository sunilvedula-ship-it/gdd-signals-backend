import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, FlatList, ActivityIndicator, RefreshControl, TouchableOpacity, Alert, useWindowDimensions } from 'react-native';
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

const isPastIntradayCutoff = (signal) => {
  if ((signal.trade_type || 'INTRADAY').toUpperCase() !== 'INTRADAY' || !signal.timestamp) return false;
  const signalTime = new Date(signal.timestamp);
  if (isNaN(signalTime.getTime())) return false;
  const cutoff = new Date(signalTime);
  cutoff.setHours(15, 15, 0, 0);
  return new Date() >= cutoff;
};

export default function FeedScreen({ session, purgeTrigger }) {
  const { width, height } = useWindowDimensions();
  const useTwoColumns = width >= 900 && width > height;
  const [signals, setSignals] = useState([]);
  const [brokerStatus, setBrokerStatus] = useState({ status: 'sandbox', broker_name: 'Sandbox Broker', balance: 1000000, mode: 'SANDBOX', combined_open_pnl: 0 });
  const [positions, setPositions] = useState([]);
  const [mutedSymbols, setMutedSymbols] = useState([]);
  const [consentSigned, setConsentSigned] = useState(true);
  
  const [selectedLots, setSelectedLots] = useState({});
  const [selectedTradeTypes, setSelectedTradeTypes] = useState({});
  const [selectedModes, setSelectedModes] = useState({});
  const [submitting, setSubmitting] = useState({});
  
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchSignals = async () => {
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }

      // Helper: safely parse JSON, returns null on failure
      const safeJson = async (res, label) => {
        if (!res.ok) {
          console.warn(`${label} returned HTTP ${res.status}`);
          return null;
        }
        const text = await res.text();
        try {
          return JSON.parse(text);
        } catch {
          console.warn(`${label} returned non-JSON:`, text.substring(0, 120));
          return null;
        }
      };
      
      const response = await fetch(`${BACKEND_URL}/api/signals`, { headers });
      const data = await safeJson(response, 'signals');
      if (Array.isArray(data)) setSignals(data);
      
      // Fetch broker account status
      const brokerRes = await fetch(`${BACKEND_URL}/api/broker/status`, { headers });
      const brokerData = await safeJson(brokerRes, 'broker/status');
      if (brokerData) setBrokerStatus(brokerData);
      
      // Fetch positions (to check active trade running on signal_id)
      const positionsRes = await fetch(`${BACKEND_URL}/api/paper-trades`, { headers });
      const positionsData = await safeJson(positionsRes, 'paper-trades');
      if (positionsData) setPositions(positionsData.positions || []);

      // Fetch settings for muted symbols
      const settingsRes = await fetch(`${BACKEND_URL}/api/user/settings`, { headers });
      const settingsData = await safeJson(settingsRes, 'user/settings');
      if (settingsData) setMutedSymbols(settingsData.muted_symbols || []);

      // Fetch daily consent status
      const consentRes = await fetch(`${BACKEND_URL}/api/consent`, { headers });
      const consentData = await safeJson(consentRes, 'consent');
      if (consentData && consentData.consent_signed !== undefined) {
        setConsentSigned(consentData.consent_signed);
      }
    } catch (error) {
      console.warn("Network error loading signals & broker info:", error.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchSignals();
    // Poll signals every 5 seconds for real-time responsiveness
    const timer = setInterval(fetchSignals, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (purgeTrigger > 0) {
      setSignals([]);
      setPositions([]);
      fetchSignals();
    }
  }, [purgeTrigger]);

  const onRefresh = () => {
    setRefreshing(true);
    fetchSignals();
  };

  const getLotSize = (symbol) => {
    const sym = (symbol || '').toUpperCase();
    if (sym.includes('BANKNIFTY')) return 30;
    if (sym.includes('NIFTY')) return 65;
    if (sym.includes('SENSEX') || sym.includes('BSX')) return 20;
    if (sym.includes('CRUDE')) return 100;
    if (sym.includes('GOLD')) return 100;
    if (sym.includes('WIPRO')) return 1500;
    if (sym.includes('RELIANCE')) return 250;
    if (sym.includes('TITAN')) return 375;
    if (sym.includes('BAJFINSERV')) return 500;
    if (sym.includes('ADANIPORTS')) return 625;
    return 100; // default fallback
  };

  const isCryptoAsset = (symbol) => {
    const sym = (symbol || '').toUpperCase();
    return sym.includes('BTC') || sym.includes('ETH') || sym.includes('SOL') || sym.includes('USD') || sym.includes('USDT');
  };

  const submitOrder = async (signalId, previewToken = null) => {
    const lots = selectedLots[signalId] || 1;
    const tradeType = selectedTradeTypes[signalId] || 'FUTURE';
    const mode = selectedModes[signalId] || 'PAPER';
    
    setSubmitting(prev => ({ ...prev, [signalId]: true }));
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      const response = await fetch(`${BACKEND_URL}/api/broker/execute`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          signal_id: signalId,
          trade_type: tradeType,
          mode: mode,
          lots: lots,
          preview_token: previewToken,
          idempotency_key: previewToken ? previewToken.slice(-80) : undefined,
        })
      });
      const text = await response.text();
      let resData;
      try { resData = JSON.parse(text); } catch { resData = null; }
      if (response.ok && resData) {
        const title = resData.mode === 'LIVE' ? 'Order submitted' : 'Paper trade opened';
        Alert.alert(title, `${resData.symbol}\n${lots} Lots (${resData.qty} Qty)\n${resData.order_status}`);
        fetchSignals();
      } else {
        Alert.alert('Order failed', resData?.detail || 'Order execution failed');
      }
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setSubmitting(prev => ({ ...prev, [signalId]: false }));
    }
  };

  const handleExecuteOrder = async (signalId) => {
    const lots = selectedLots[signalId] || 1;
    const tradeType = selectedTradeTypes[signalId] || 'FUTURE';
    const mode = selectedModes[signalId] || 'PAPER';
    if (mode !== 'LIVE') {
      await submitOrder(signalId);
      return;
    }
    if (!brokerStatus.live_enabled) {
      Alert.alert('Live trading unavailable', 'Connect an approved Alice Blue account before placing live orders.');
      return;
    }
    if (!consentSigned) {
      Alert.alert('Consent required', 'Sign today\'s trading consent before placing a live order.');
      return;
    }

    setSubmitting(prev => ({ ...prev, [signalId]: true }));
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
      const response = await fetch(`${BACKEND_URL}/api/broker/order-preview`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ signal_id: signalId, trade_type: tradeType, mode, lots }),
      });
      const text = await response.text();
      let preview;
      try { preview = JSON.parse(text); } catch { preview = null; }
      if (!response.ok || !preview?.preview_token) {
        Alert.alert('Preview failed', preview?.detail || 'The live order could not be prepared.');
        return;
      }
      Alert.alert(
        'Confirm Live Order',
        `${preview.transaction_type} ${preview.symbol}\n${preview.quantity} Qty (${preview.lots} Lots)\nLIMIT at Rs ${Number(preview.limit_price).toFixed(2)}\n${preview.product}`,
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Place Order',
            onPress: () => submitOrder(signalId, preview.preview_token),
          },
        ],
      );
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setSubmitting(prev => ({ ...prev, [signalId]: false }));
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

  const renderItem = ({ item }) => {
    const isLong = item.action === 'LONG' || item.action === 'BUY';
    const isShort = item.action === 'SHORT'; // Sell and Cover are exits, do not allow short entry on Sell
    
    let cardStyle = styles.cardNeutral;
    let badgeStyle = styles.badgeNeutral;
    if (isLong) {
      cardStyle = styles.cardLong;
      badgeStyle = styles.badgeLong;
    } else if (isShort) {
      cardStyle = styles.cardShort;
      badgeStyle = styles.badgeShort;
    }

    const symUpper = (item.symbol || '').toUpperCase();
    const isUSD = symUpper.includes('USD') || symUpper.includes('USDT') || ['BTC', 'ETH', 'SOL', 'ADA', 'XRP'].includes(symUpper);
    const currencySymbol = isUSD ? '$' : '₹';
    const locale = isUSD ? 'en-US' : 'en-IN';

    // Lot execution panel states
    const lots = selectedLots[item.id] || 1;
    const tradeType = selectedTradeTypes[item.id] || 'FUTURE';
    const mode = selectedModes[item.id] || 'PAPER';
    const isCrypto = isCryptoAsset(item.symbol);
    const lotSizeVal = getLotSize(item.symbol);
    const totalQty = isCrypto ? lots : (lots * lotSizeVal);
    const qtyLabel = isCrypto ? `${totalQty} Qty` : `${lots} Lots (${totalQty} Qty)`;

    // Option symbol identification
    const isOptionSymbol = (sym) => (sym || '').includes(' CE') || (sym || '').includes(' PE');
    const activePositionStatuses = ['OPEN', 'PENDING', 'PARTIAL', 'EXIT_PENDING', 'EXIT_PARTIAL'];
    const existingFuturePos = positions.find(pos => pos.signal_id === item.id && activePositionStatuses.includes(pos.status) && !isOptionSymbol(pos.symbol));
    const existingOptionPos = positions.find(pos => pos.signal_id === item.id && activePositionStatuses.includes(pos.status) && isOptionSymbol(pos.symbol));
    
    const currentTypePos = tradeType === 'FUTURE' ? existingFuturePos : existingOptionPos;
    const isTradeRunningForSelectedType = !!currentTypePos;

    // Check if this signal is still active (no subsequent exit alert on the symbol matching direction)
    const exitActions = ['EXIT', 'CLOSE'];
    if (isLong) {
      exitActions.push('EXIT_LONG', 'SELL');
    } else if (isShort) {
      exitActions.push('EXIT_SHORT', 'COVER');
    }

    const exitExists = signals.some(s => 
      s.symbol === item.symbol && 
      exitActions.includes(s.action) && 
      s.id > item.id
    );
    const cutoffReached = isPastIntradayCutoff(item);
    const isActiveSignal = !exitExists && !cutoffReached && (isLong || isShort);

    const baseSymbol = getNormalizedBaseSymbol(item.symbol);
    const isMuted = mutedSymbols.includes(baseSymbol);

    return (
      <View style={[styles.card, useTwoColumns && styles.cardWide, cardStyle, isMuted && { opacity: 0.6 }]}>
        <View style={styles.cardHeader}>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Text style={styles.symbol}>{item.symbol}</Text>
            <TouchableOpacity onPress={() => handleToggleMute(item.symbol)} style={{ marginLeft: 8, padding: 4 }}>
              <Text style={{ fontSize: 14 }}>{isMuted ? '🔕' : '🔔'}</Text>
            </TouchableOpacity>
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            {item.timeframe && (
              <View style={[styles.badge, styles.badgeTimeframe, { marginRight: 6 }]}>
                <Text style={styles.badgeText}>⏱ {item.timeframe}</Text>
              </View>
            )}
            {item.trade_type && (
              <View style={[
                styles.badge, 
                { 
                  backgroundColor: item.trade_type === 'POSITIONAL' ? 'rgba(59, 130, 246, 0.12)' : 'rgba(16, 185, 129, 0.12)', 
                  borderColor: item.trade_type === 'POSITIONAL' ? '#3b82f6' : '#10b981', 
                  borderWidth: 1, 
                  marginRight: 6 
                }
              ]}>
                <Text style={[
                  styles.badgeText, 
                  { color: item.trade_type === 'POSITIONAL' ? '#3b82f6' : '#10b981', fontSize: 9 }
                ]}>
                  {item.trade_type}
                </Text>
              </View>
            )}
            {isMuted && (
              <View style={[styles.badge, { backgroundColor: '#4b5563', marginRight: 6 }]}>
                <Text style={styles.badgeText}>MUTED</Text>
              </View>
            )}
            <View style={[styles.badge, badgeStyle]}>
              <Text style={styles.badgeText}>{item.action}</Text>
            </View>
          </View>
        </View>
        <View style={styles.cardBody}>
          <View>
            <Text style={styles.priceLabel}>ENTRY PRICE</Text>
            <Text style={styles.price}>{currencySymbol}{item.price.toLocaleString(locale, {minimumFractionDigits: 2})}</Text>
          </View>
          <View style={styles.meta}>
            <Text style={styles.source}>{item.source_name}</Text>
            <Text style={styles.time}>{formatSignalDate(item.timestamp)}</Text>
          </View>
        </View>

        {/* Dynamic Execution Panel */}
        {isActiveSignal ? (
          <View style={styles.execPanel}>
            {/* If a trade is running for this selected contract type, show the locked banner */}
            {isTradeRunningForSelectedType ? (
              <View style={[styles.execPanelLocked, { marginTop: 0, marginBottom: 8 }]}>
                <Text style={styles.lockedText}>
                  ⚠️ {tradeType} trade running on this signal with {currentTypePos.lot_size || 1} Lots
                </Text>
              </View>
            ) : null}

            {/* Lot Selector */}
            <View style={[styles.lotRow, isTradeRunningForSelectedType && { opacity: 0.5 }]}>
              <Text style={styles.lotLabel}>LOTS:</Text>
              <View style={styles.lotControls}>
                <TouchableOpacity 
                  style={styles.lotBtn} 
                  onPress={() => !isTradeRunningForSelectedType && setSelectedLots(prev => ({ ...prev, [item.id]: Math.max(1, (prev[item.id] || 1) - 1) }))}
                  disabled={isTradeRunningForSelectedType}
                >
                  <Text style={styles.lotBtnText}>-</Text>
                </TouchableOpacity>
                <Text style={styles.lotValue}>{lots}</Text>
                <TouchableOpacity 
                  style={styles.lotBtn} 
                  onPress={() => !isTradeRunningForSelectedType && setSelectedLots(prev => ({ ...prev, [item.id]: (prev[item.id] || 1) + 1 }))}
                  disabled={isTradeRunningForSelectedType}
                >
                  <Text style={styles.lotBtnText}>+</Text>
                </TouchableOpacity>
              </View>
              <Text style={styles.qtyBrackets}>({qtyLabel})</Text>
            </View>

            {/* Trade Toggles */}
            <View style={styles.toggleRow}>
              <View style={styles.toggleGroup}>
                <TouchableOpacity 
                  style={[styles.toggleBtn, tradeType === 'FUTURE' && styles.toggleActive]}
                  onPress={() => setSelectedTradeTypes(prev => ({ ...prev, [item.id]: 'FUTURE' }))}
                >
                  <Text style={[styles.toggleBtnText, tradeType === 'FUTURE' && styles.toggleActiveText]}>FUTURE</Text>
                </TouchableOpacity>
                <TouchableOpacity 
                  style={[styles.toggleBtn, tradeType === 'OPTION' && styles.toggleActive]}
                  onPress={() => setSelectedTradeTypes(prev => ({ ...prev, [item.id]: 'OPTION' }))}
                >
                  <Text style={[styles.toggleBtnText, tradeType === 'OPTION' && styles.toggleActiveText]}>OPTION</Text>
                </TouchableOpacity>
              </View>
              
              <View style={[styles.toggleGroup, isTradeRunningForSelectedType && { opacity: 0.5 }]}>
                <TouchableOpacity 
                  style={[styles.toggleBtn, mode === 'PAPER' && styles.toggleActiveMode]}
                  onPress={() => !isTradeRunningForSelectedType && setSelectedModes(prev => ({ ...prev, [item.id]: 'PAPER' }))}
                  disabled={isTradeRunningForSelectedType}
                >
                  <Text style={[styles.toggleBtnText, mode === 'PAPER' && styles.toggleActiveText]}>PAPER</Text>
                </TouchableOpacity>
                <TouchableOpacity 
                  style={[
                    styles.toggleBtn,
                    mode === 'LIVE' && styles.toggleActiveModeLive,
                    !brokerStatus.live_enabled && { opacity: 0.4 },
                  ]}
                  onPress={() => !isTradeRunningForSelectedType && brokerStatus.live_enabled && setSelectedModes(prev => ({ ...prev, [item.id]: 'LIVE' }))}
                  disabled={isTradeRunningForSelectedType || !brokerStatus.live_enabled}
                >
                  <Text style={[styles.toggleBtnText, mode === 'LIVE' && styles.toggleActiveText]}>LIVE</Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* Execute Button */}
            {!isTradeRunningForSelectedType ? (
              <TouchableOpacity 
                style={styles.executeBtn}
                onPress={() => handleExecuteOrder(item.id, item.symbol)}
                disabled={submitting[item.id]}
              >
                <Text style={styles.executeBtnText}>
                  {submitting[item.id] ? 'EXECUTING...' : 'EXECUTE ORDER'}
                </Text>
              </TouchableOpacity>
            ) : null}
          </View>
        ) : (isLong || isShort) ? (
          <View style={styles.execPanelLocked}>
            <Text style={styles.lockedTextExpired}>
              {cutoffReached ? 'Intraday closed at 3:15 PM' : 'Signal inactive (trend exited)'}
            </Text>
          </View>
        ) : (
          <View style={styles.execPanelLocked}>
            <Text style={styles.lockedTextExpired}>Exit Signal (No Entry Allowed)</Text>
          </View>
        )}
      </View>
    );
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  const combinedPnl = brokerStatus.combined_open_pnl || 0;
  const pnlColor = combinedPnl > 0 ? '#10b981' : (combinedPnl < 0 ? '#ef4444' : '#ffffff');

  return (
    <View style={styles.container}>
      {/* Account Overview Sticky Banner */}
      <View style={styles.bannerContainer}>
        <View style={styles.bannerLeft}>
          <Text style={styles.bannerLabel}>{brokerStatus.broker_name.toUpperCase()}</Text>
          <Text style={styles.bannerBalance}>
            ₹{brokerStatus.balance.toLocaleString('en-IN', {minimumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.bannerRight}>
          <Text style={styles.bannerLabel}>COMBINED OPEN P&L</Text>
          <Text style={[styles.bannerPnl, { color: pnlColor }]}>
            {combinedPnl >= 0 ? '+' : ''}₹{combinedPnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
          </Text>
        </View>
      </View>

      {brokerStatus.mode === 'LIVE' && !consentSigned && (
        <View style={styles.consentWarningBanner}>
          <Text style={styles.consentWarningText}>
            ⚠️ Auto-Trading Paused: Please sign today's daily consent on the Consent tab.
          </Text>
        </View>
      )}

      <FlatList
        key={useTwoColumns ? 'two-columns' : 'one-column'}
        data={signals}
        numColumns={useTwoColumns ? 2 : 1}
        columnWrapperStyle={useTwoColumns ? styles.cardRow : undefined}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3b82f6" />
        }
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No trading signals received yet.</Text>
            <Text style={styles.emptySubtext}>Ensure your TradingView webhook or Python scanner is connected.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0a0e17',
    padding: 16,
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0a0e17',
    justifyContent: 'center',
    alignItems: 'center',
  },
  card: {
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderLeftWidth: 4,
  },
  cardWide: {
    flex: 1,
  },
  cardRow: {
    gap: 12,
  },
  cardLong: {
    borderLeftColor: '#10b981',
  },
  cardShort: {
    borderLeftColor: '#ef4444',
  },
  cardNeutral: {
    borderLeftColor: '#64748b',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  symbol: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  badgeLong: {
    backgroundColor: 'rgba(16, 185, 129, 0.15)',
  },
  badgeShort: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
  },
  badgeNeutral: {
    backgroundColor: 'rgba(100, 116, 139, 0.15)',
  },
  badgeTimeframe: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    borderColor: 'rgba(255, 255, 255, 0.15)',
    borderWidth: 1,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  cardBody: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginBottom: 4,
  },
  priceLabel: {
    fontSize: 9,
    color: '#9ca3af',
    marginBottom: 4,
  },
  price: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  meta: {
    alignItems: 'flex-end',
  },
  source: {
    fontSize: 11,
    fontWeight: '500',
    color: '#ffffff',
    marginBottom: 2,
  },
  time: {
    fontSize: 11,
    color: '#9ca3af',
  },
  emptyContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 60,
  },
  emptyText: {
    fontSize: 15,
    fontWeight: 'bold',
    color: '#9ca3af',
    marginBottom: 6,
  },
  emptySubtext: {
    fontSize: 12,
    color: '#6b7280',
    textAlign: 'center',
    paddingHorizontal: 30,
  },
  bannerContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 12,
  },
  bannerLeft: {
    flex: 1.2,
  },
  bannerRight: {
    flex: 1,
    alignItems: 'flex-end',
  },
  bannerLabel: {
    fontSize: 8,
    fontWeight: 'bold',
    color: '#9ca3af',
    marginBottom: 4,
  },
  bannerBalance: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  bannerPnl: {
    fontSize: 14,
    fontWeight: 'bold',
  },
  execPanel: {
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderColor: 'rgba(255, 255, 255, 0.05)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginTop: 10,
  },
  execPanelLocked: {
    backgroundColor: 'rgba(255, 255, 255, 0.01)',
    borderColor: 'rgba(255, 255, 255, 0.03)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 8,
    marginTop: 10,
    alignItems: 'center',
  },
  lockedText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#fbbf24',
  },
  lockedTextExpired: {
    fontSize: 10,
    color: '#6b7280',
    fontStyle: 'italic',
  },
  lotRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  lotLabel: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#ffffff',
    marginRight: 8,
  },
  lotControls: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
    borderRadius: 6,
    padding: 2,
  },
  lotBtn: {
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    borderRadius: 4,
    width: 20,
    height: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  lotBtnText: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  lotValue: {
    fontSize: 11,
    fontWeight: 'bold',
    color: '#ffffff',
    paddingHorizontal: 8,
  },
  qtyBrackets: {
    fontSize: 9,
    color: '#9ca3af',
    marginLeft: 8,
  },
  toggleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  toggleGroup: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.05)',
    padding: 2,
    width: '48%',
  },
  toggleBtn: {
    flex: 1,
    paddingVertical: 4,
    borderRadius: 4,
    alignItems: 'center',
  },
  toggleActive: {
    backgroundColor: '#3b82f6',
  },
  toggleActiveMode: {
    backgroundColor: '#6b7280',
  },
  toggleActiveModeLive: {
    backgroundColor: '#10b981',
  },
  toggleBtnText: {
    fontSize: 9,
    fontWeight: 'bold',
    color: '#9ca3af',
  },
  toggleActiveText: {
    color: '#ffffff',
  },
  executeBtn: {
    backgroundColor: '#10b981',
    borderRadius: 6,
    paddingVertical: 8,
    alignItems: 'center',
  },
  executeBtnText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  consentWarningBanner: {
    backgroundColor: 'rgba(239, 68, 68, 0.1)',
    borderColor: 'rgba(239, 68, 68, 0.25)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 12,
    alignItems: 'center',
  },
  consentWarningText: {
    color: '#ef4444',
    fontSize: 11,
    fontWeight: 'bold',
    textAlign: 'center',
    lineHeight: 16,
  },
});

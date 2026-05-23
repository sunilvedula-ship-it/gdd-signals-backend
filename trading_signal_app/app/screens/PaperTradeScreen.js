import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, FlatList, TouchableOpacity, ActivityIndicator } from 'react-native';
import { BACKEND_URL } from '../config';


export default function PaperTradeScreen() {
  const [positions, setPositions] = useState([]);
  const [stats, setStats] = useState({ total_pnl: 0, win_rate: 0, total_trades: 0 });
  const [loading, setLoading] = useState(true);

  const fetchPaperTrades = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/paper-trades`);
      const data = await response.json();
      setPositions(data.positions);
      setStats(data.stats);
    } catch (error) {
      console.error("Error loading paper trades:", error);
    } finally {
      setLoading(false);
    }
  };

  const closePosition = async (id) => {
    try {
      await fetch(`${BACKEND_URL}/api/paper-trades/manual-exit/${id}`, { method: 'POST' });
      fetchPaperTrades();
    } catch (error) {
      console.error("Error closing trade:", error);
    }
  };

  useEffect(() => {
    fetchPaperTrades();
    const timer = setInterval(fetchPaperTrades, 5000);
    return () => clearInterval(timer);
  }, []);

  const renderItem = ({ item }) => {
    const isLong = item.direction === 'LONG';
    const isClosed = item.status === 'CLOSED';
    const isProfit = item.pnl > 0;
    
    return (
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <View style={styles.leftHeader}>
            <View style={[styles.dirBadge, isLong ? styles.dirLong : styles.dirShort]}>
              <Text style={styles.dirText}>{item.direction}</Text>
            </View>
            <Text style={styles.symbol}>{item.symbol}</Text>
            <Text style={styles.qty}>{item.qty} Qty</Text>
          </View>
          <Text style={[styles.pnl, isClosed ? (isProfit ? styles.textProfit : styles.textLoss) : styles.textOpen]}>
            {isClosed ? `₹${item.pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}` : 'OPEN'}
          </Text>
        </View>
        
        <View style={styles.cardDetails}>
          <Text style={styles.detailText}>Entry: ₹{item.entry_price.toLocaleString('en-IN')}</Text>
          {isClosed ? (
            <Text style={styles.detailText}>Exit: ₹{item.exit_price.toLocaleString('en-IN')}</Text>
          ) : (
            <TouchableOpacity style={styles.exitBtn} onPress={() => closePosition(item.id)}>
              <Text style={styles.exitBtnText}>Close</Text>
            </TouchableOpacity>
          )}
        </View>
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

  const pnlStyle = stats.total_pnl > 0 ? styles.textProfit : (stats.total_pnl < 0 ? styles.textLoss : styles.textNeutral);

  return (
    <View style={styles.container}>
      <View style={styles.statsContainer}>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>TOTAL P&L</Text>
          <Text style={[styles.statValue, pnlStyle]}>
            ₹{stats.total_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
          </Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>WIN RATE</Text>
          <Text style={styles.statValue}>{stats.win_rate.toFixed(1)}%</Text>
        </View>
        <View style={styles.statBox}>
          <Text style={styles.statLabel}>TRADES</Text>
          <Text style={styles.statValue}>{stats.total_trades}</Text>
        </View>
      </View>

      <Text style={styles.title}>Positions Ledger</Text>
      
      <FlatList
        data={positions}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No positions active or history logged.</Text>
            <Text style={styles.emptySubtext}>Sourced signals will execute simulated trades here.</Text>
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
    align-items: 'center',
  },
  statsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 20,
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
    marginBottom: 12,
  },
  card: {
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.06)',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    align-items: 'center',
    marginBottom: 8,
  },
  leftHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dirBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    marginRight: 8,
  },
  dirLong: {
    backgroundColor: 'rgba(16, 185, 129, 0.15)',
  },
  dirShort: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
  },
  dirText: {
    fontSize: 9,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  symbol: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#ffffff',
    marginRight: 8,
  },
  qty: {
    fontSize: 11,
    color: '#9ca3af',
  },
  pnl: {
    fontSize: 14,
    fontWeight: 'bold',
  },
  textProfit: { color: '#10b981' },
  textLoss: { color: '#ef4444' },
  textNeutral: { color: '#ffffff' },
  textOpen: { color: '#3b82f6' },
  cardDetails: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    align-items: 'center',
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.04)',
    paddingTop: 8,
    marginTop: 4,
  },
  detailText: {
    fontSize: 11,
    color: '#9ca3af',
  },
  exitBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    borderColor: 'rgba(239, 68, 68, 0.3)',
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  exitBtnText: {
    color: '#ef4444',
    fontSize: 10,
    fontWeight: 'bold',
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
  },
});

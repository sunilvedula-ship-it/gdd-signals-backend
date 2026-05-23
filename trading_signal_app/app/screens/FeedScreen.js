import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, FlatList, ActivityIndicator, RefreshControl } from 'react-native';
import { BACKEND_URL } from '../config';


export default function FeedScreen() {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchSignals = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/signals`);
      const data = await response.json();
      setSignals(data);
    } catch (error) {
      console.error("Error loading signals:", error);
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

  const onRefresh = () => {
    setRefreshing(true);
    fetchSignals();
  };

  const renderItem = ({ item }) => {
    const isLong = item.action === 'LONG' || item.action === 'BUY';
    const isShort = item.action === 'SHORT' || item.action === 'SELL';
    
    let cardStyle = styles.cardNeutral;
    let badgeStyle = styles.badgeNeutral;
    if (isLong) {
      cardStyle = styles.cardLong;
      badgeStyle = styles.badgeLong;
    } else if (isShort) {
      cardStyle = styles.cardShort;
      badgeStyle = styles.badgeShort;
    }

    return (
      <View style={[styles.card, cardStyle]}>
        <View style={styles.cardHeader}>
          <Text style={styles.symbol}>{item.symbol}</Text>
          <View style={[styles.badge, badgeStyle]}>
            <Text style={styles.badgeText}>{item.action}</Text>
          </View>
        </View>
        <View style={styles.cardBody}>
          <View>
            <Text style={styles.priceLabel}>ENTRY PRICE</Text>
            <Text style={styles.price}>₹{item.price.toLocaleString('en-IN', {minimumFractionDigits: 2})}</Text>
          </View>
          <View style={styles.meta}>
            <Text style={styles.source}>{item.source_name}</Text>
            <Text style={styles.time}>{new Date(item.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</Text>
          </View>
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

  return (
    <View style={styles.container}>
      <FlatList
        data={signals}
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
    align-items: 'center',
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
    align-items: 'center',
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
  badgeText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#ffffff',
  },
  cardBody: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    align-items: 'flex-end',
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
});

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  AppState,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';

import { BACKEND_URL } from '../config';
import { supabase } from '../supabase';


export default function SettingsScreen({ session, onPurge }) {
  const [brokers, setBrokers] = useState([]);
  const [loading, setLoading] = useState(true);

  const authHeaders = (json = false) => {
    const headers = json ? { 'Content-Type': 'application/json' } : {};
    if (session?.access_token) {
      headers.Authorization = `Bearer ${session.access_token}`;
    }
    return headers;
  };

  const fetchCredentials = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/credentials`, {
        headers: authHeaders(),
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (response.ok && data) {
        setBrokers(data.brokers || []);
      }
    } catch (error) {
      console.warn('Error loading broker connection:', error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentials();
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') fetchCredentials();
    });
    return () => subscription.remove();
  }, [session?.access_token]);

  const connectAliceBlue = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/broker/aliceblue/login-url`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok || !data?.login_url) {
        Alert.alert('Connection unavailable', data?.detail || 'Alice Blue login could not be started.');
        return;
      }
      await Linking.openURL(data.login_url);
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setLoading(false);
    }
  };

  const disconnectAliceBlue = () => {
    Alert.alert(
      'Disconnect Alice Blue',
      'Live orders will remain unavailable until the broker account is connected again.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: async () => {
            setLoading(true);
            try {
              await fetch(`${BACKEND_URL}/api/credentials/aliceblue`, {
                method: 'DELETE',
                headers: authHeaders(),
              });
              await fetchCredentials();
            } catch (error) {
              Alert.alert('Network error', error.message);
            } finally {
              setLoading(false);
            }
          },
        },
      ],
    );
  };

  const handlePurgeData = () => {
    Alert.alert(
      'Confirm Database Purge',
      'This permanently deletes all signals and positions from the database.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Purge Data',
          style: 'destructive',
          onPress: async () => {
            setLoading(true);
            try {
              const response = await fetch(`${BACKEND_URL}/api/admin/purge-test-data`, {
                method: 'POST',
                headers: authHeaders(true),
              });
              const data = await response.json();
              if (!response.ok) {
                Alert.alert('Purge failed', data?.detail || 'Could not clear data.');
                return;
              }
              if (onPurge) onPurge();
              Alert.alert('Purge complete', `${data.purged_signals} signals and ${data.purged_positions} positions deleted.`);
            } catch (error) {
              Alert.alert('Network error', error.message);
            } finally {
              setLoading(false);
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  const aliceBlue = brokers.find(broker => broker.id === 'aliceblue');

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>Broker Account</Text>
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <View style={styles.rowText}>
            <Text style={styles.cardTitle}>Alice Blue</Text>
            <Text style={styles.cardText}>Vendor API execution account</Text>
          </View>
          <TouchableOpacity style={styles.outlineButton} onPress={() => Linking.openURL('https://aliceblueonline.com/')}>
            <Text style={styles.outlineButtonText}>OPEN</Text>
          </TouchableOpacity>
        </View>
      </View>

      <Text style={styles.sectionTitle}>Broker Connection</Text>
      <View style={styles.card}>
        {aliceBlue?.configured ? (
          <>
            <View style={styles.statusRow}>
              <View style={styles.statusDot} />
              <View>
                <Text style={styles.connectedTitle}>Alice Blue connected</Text>
                <Text style={styles.cardText}>Account authorized through secure broker login</Text>
              </View>
            </View>
            <TouchableOpacity style={styles.dangerButton} onPress={disconnectAliceBlue}>
              <Text style={styles.dangerButtonText}>DISCONNECT</Text>
            </TouchableOpacity>
          </>
        ) : (
          <TouchableOpacity style={styles.primaryButton} onPress={connectAliceBlue}>
            <Text style={styles.primaryButtonText}>CONNECT ALICE BLUE</Text>
          </TouchableOpacity>
        )}
      </View>

      <Text style={styles.sectionTitle}>Strategies</Text>
      <View style={styles.card}>
        {[
          ['JGD BNF Option Buying', 'https://tradetron.tech/strategy/5985482'],
          ['Sensex Option Buying', 'https://tradetron.tech/strategy/8049757'],
          ['Banknifty Futures Positional', 'https://tradetron.tech/strategy/2547331'],
          ['Nifty Futures Positional', 'https://tradetron.tech/strategy/2769106'],
        ].map(([label, url]) => (
          <TouchableOpacity key={url} style={styles.strategyRow} onPress={() => Linking.openURL(url)}>
            <Text style={styles.strategyText}>{label}</Text>
            <Text style={styles.strategyAction}>OPEN</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.sectionTitle}>Account</Text>
      <TouchableOpacity style={styles.dangerButton} onPress={() => supabase.auth.signOut()}>
        <Text style={styles.dangerButtonText}>LOG OUT</Text>
      </TouchableOpacity>

      <Text style={styles.sectionTitle}>Testing</Text>
      <TouchableOpacity style={styles.dangerButton} onPress={handlePurgeData}>
        <Text style={styles.dangerButtonText}>PURGE TEST DATA</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}


const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0e17' },
  content: { padding: 16, paddingBottom: 32 },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0a0e17',
    justifyContent: 'center',
    alignItems: 'center',
  },
  sectionTitle: {
    color: '#9ca3af',
    fontSize: 12,
    fontWeight: '700',
    marginTop: 14,
    marginBottom: 8,
    textTransform: 'uppercase',
  },
  card: {
    backgroundColor: '#101722',
    borderColor: '#273244',
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
    marginBottom: 8,
  },
  cardTitle: { color: '#f8fafc', fontSize: 15, fontWeight: '700', marginBottom: 4 },
  cardText: { color: '#94a3b8', fontSize: 12, lineHeight: 17 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  rowText: { flex: 1, paddingRight: 12 },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
  statusDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: '#10b981', marginRight: 10 },
  connectedTitle: { color: '#10b981', fontSize: 14, fontWeight: '700', marginBottom: 3 },
  primaryButton: {
    backgroundColor: '#2563eb',
    borderRadius: 6,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryButtonText: { color: '#ffffff', fontSize: 12, fontWeight: '700' },
  outlineButton: {
    borderWidth: 1,
    borderColor: '#3b82f6',
    borderRadius: 6,
    minWidth: 64,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  outlineButtonText: { color: '#60a5fa', fontSize: 11, fontWeight: '700' },
  dangerButton: {
    backgroundColor: '#24151a',
    borderWidth: 1,
    borderColor: '#7f1d1d',
    borderRadius: 6,
    minHeight: 42,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
  },
  dangerButtonText: { color: '#f87171', fontSize: 11, fontWeight: '700' },
  strategyRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    minHeight: 42,
    borderBottomColor: '#273244',
    borderBottomWidth: 1,
  },
  strategyText: { color: '#e5e7eb', fontSize: 12, flex: 1, paddingRight: 10 },
  strategyAction: { color: '#60a5fa', fontSize: 10, fontWeight: '700' },
});

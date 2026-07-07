import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  AppState,
  Linking,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  useWindowDimensions,
  View,
} from 'react-native';

import { BACKEND_URL } from '../config';
import { supabase } from '../supabase';


export default function SettingsScreen({ session, onLogout, onPurge }) {
  const [brokers, setBrokers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedBrokerId, setSelectedBrokerId] = useState('aliceblue');
  const [savingBroker, setSavingBroker] = useState(false);
  const [authorizingBroker, setAuthorizingBroker] = useState(false);
  const [savingStaticIp, setSavingStaticIp] = useState(false);
  const [liveReadiness, setLiveReadiness] = useState(null);
  const [staticIp, setStaticIp] = useState('');
  const [staticIpRegistered, setStaticIpRegistered] = useState(false);
  const [flattradeClientId, setFlattradeClientId] = useState('');
  const [flattradeApiKey, setFlattradeApiKey] = useState('');
  const [flattradeApiSecret, setFlattradeApiSecret] = useState('');
  const { width, height } = useWindowDimensions();
  const isLandscape = width > height;

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
      await fetchLiveReadiness(selectedBrokerId, false);
    } catch (error) {
      console.warn('Error loading broker connection:', error.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchLiveReadiness = async (brokerId = selectedBrokerId, refreshIp = false) => {
    const response = await fetch(`${BACKEND_URL}/api/broker/live-readiness?broker_id=${brokerId}&refresh_ip=${refreshIp ? 'true' : 'false'}`, {
      headers: authHeaders(),
    });
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = null; }
    if (response.ok && data) {
      setLiveReadiness(data);
      setStaticIp(data.static_ip || '');
      setStaticIpRegistered(!!data.static_ip_registered);
    }
    return data;
  };

  useEffect(() => {
    fetchCredentials();
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') fetchCredentials();
    });
    return () => subscription.remove();
  }, [session?.access_token]);

  useEffect(() => {
    fetchLiveReadiness(selectedBrokerId, false);
  }, [selectedBrokerId]);

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
    disconnectBroker('aliceblue', 'Alice Blue');
  };

  const disconnectBroker = (brokerId, brokerName) => {
    Alert.alert(
      `Disconnect ${brokerName}`,
      'Live orders will remain unavailable until the broker account is connected again.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: async () => {
            setLoading(true);
            try {
              await fetch(`${BACKEND_URL}/api/credentials/${brokerId}`, {
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

  const saveFlattradeCredentials = async () => {
    if (!flattradeClientId.trim() || !flattradeApiKey.trim() || !flattradeApiSecret.trim()) {
      Alert.alert('Flattrade details required', 'Enter Client ID, API Key and API Secret.');
      return;
    }
    setSavingBroker(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/credentials`, {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({
          broker_id: 'flattrade',
          api_key: flattradeApiKey.trim(),
          api_secret: flattradeApiSecret.trim(),
          extra: { client_id: flattradeClientId.trim() },
        }),
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok) {
        Alert.alert('Credentials not saved', data?.detail || 'Could not save Flattrade credentials.');
        return;
      }
      setSelectedBrokerId('flattrade');
      setFlattradeApiSecret('');
      await fetchCredentials();
      Alert.alert('Flattrade saved', 'Authorize today\'s Flattrade session before placing live orders.');
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setSavingBroker(false);
    }
  };

  const authorizeFlattrade = async () => {
    setAuthorizingBroker(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/broker/flattrade/login-url`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok || !data?.login_url) {
        Alert.alert('Authorization unavailable', data?.detail || 'Flattrade authorization could not be started.');
        return;
      }
      await Linking.openURL(data.login_url);
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setAuthorizingBroker(false);
    }
  };

  const saveStaticIp = async () => {
    if (!staticIp.trim()) {
      Alert.alert('Static IP required', 'Enter the approved public static IP.');
      return;
    }
    setSavingStaticIp(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/broker/static-ip`, {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({
          broker_id: selectedBrokerId,
          static_ip: staticIp.trim(),
          registered_with_broker: staticIpRegistered,
        }),
      });
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      if (!response.ok || !data) {
        Alert.alert('Static IP not saved', data?.detail || 'Could not save the static IP.');
        return;
      }
      setLiveReadiness(data);
      setStaticIp(data.static_ip || staticIp.trim());
      setStaticIpRegistered(!!data.static_ip_registered);
      Alert.alert(data.live_enabled ? 'Live ready' : 'Static IP saved', data.live_enabled ? 'Live orders are enabled for this account.' : (data.blockers?.[0] || 'Complete the remaining live setup steps.'));
    } catch (error) {
      Alert.alert('Network error', error.message);
    } finally {
      setSavingStaticIp(false);
    }
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
  const flattrade = brokers.find(broker => broker.id === 'flattrade');
  const selectedBroker = brokers.find(broker => broker.id === selectedBrokerId);
  const selectedBrokerName = selectedBroker?.name || (selectedBrokerId === 'flattrade' ? 'Flattrade' : 'Alice Blue');

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={[styles.content, isLandscape && styles.contentLandscape]}
    >
      <Text style={styles.sectionTitle}>Broker Account</Text>
      <View style={styles.brokerGrid}>
        {[
          ['aliceblue', 'Alice Blue', 'Vendor API execution account', aliceBlue?.configured],
          ['flattrade', 'Flattrade', 'Pi API static-IP account', flattrade?.configured],
        ].map(([id, name, detail, configured]) => (
          <TouchableOpacity
            key={id}
            style={[styles.brokerCard, selectedBrokerId === id && styles.brokerCardSelected]}
            onPress={() => setSelectedBrokerId(id)}
          >
            <View style={styles.rowBetween}>
              <View style={styles.rowText}>
                <Text style={styles.cardTitle}>{name}</Text>
                <Text style={styles.cardText}>{detail}</Text>
              </View>
              <View style={[styles.statusPill, configured ? styles.statusPillConnected : styles.statusPillMuted]}>
                <Text style={styles.statusPillText}>{configured ? 'SAVED' : 'SETUP'}</Text>
              </View>
            </View>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.sectionTitle}>Broker Connection</Text>
      <View style={styles.card}>
        {selectedBrokerId === 'aliceblue' ? (
          aliceBlue?.configured ? (
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
          )
        ) : (
          <>
            {flattrade?.configured ? (
              <View style={styles.statusRow}>
                <View style={styles.statusDot} />
                <View style={styles.statusTextWrap}>
                  <Text style={styles.connectedTitle}>Flattrade credentials saved</Text>
                  <Text style={styles.cardText}>Authorize a fresh Flattrade session each trading day</Text>
                </View>
              </View>
            ) : null}

            <Text style={styles.inputLabel}>Client ID</Text>
            <TextInput
              style={styles.input}
              value={flattradeClientId}
              onChangeText={setFlattradeClientId}
              placeholder="Flattrade client id"
              placeholderTextColor="#64748b"
              autoCapitalize="characters"
            />
            <Text style={styles.inputLabel}>API Key</Text>
            <TextInput
              style={styles.input}
              value={flattradeApiKey}
              onChangeText={setFlattradeApiKey}
              placeholder="Flattrade API key"
              placeholderTextColor="#64748b"
              autoCapitalize="none"
            />
            <Text style={styles.inputLabel}>API Secret</Text>
            <TextInput
              style={styles.input}
              value={flattradeApiSecret}
              onChangeText={setFlattradeApiSecret}
              placeholder={flattrade?.configured ? 'Enter only when updating' : 'Flattrade API secret'}
              placeholderTextColor="#64748b"
              autoCapitalize="none"
              secureTextEntry
            />

            <View style={styles.buttonRow}>
              <TouchableOpacity
                style={[styles.primaryButton, styles.buttonFlex, savingBroker && styles.buttonDisabled]}
                onPress={saveFlattradeCredentials}
                disabled={savingBroker}
              >
                <Text style={styles.primaryButtonText}>{savingBroker ? 'SAVING' : 'SAVE FLATTRADE'}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.outlineButton, styles.buttonFlex, (!flattrade?.configured || authorizingBroker) && styles.buttonDisabled]}
                onPress={authorizeFlattrade}
                disabled={!flattrade?.configured || authorizingBroker}
              >
                <Text style={styles.outlineButtonText}>{authorizingBroker ? 'OPENING' : 'AUTHORIZE'}</Text>
              </TouchableOpacity>
            </View>

            {flattrade?.configured ? (
              <TouchableOpacity style={[styles.dangerButton, { marginTop: 10 }]} onPress={() => disconnectBroker('flattrade', 'Flattrade')}>
                <Text style={styles.dangerButtonText}>DISCONNECT FLATTRADE</Text>
              </TouchableOpacity>
            ) : null}
          </>
        )}
      </View>

      <Text style={styles.sectionTitle}>Live Readiness</Text>
      <View style={styles.card}>
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, liveReadiness?.live_enabled ? styles.statusDotLive : styles.statusDotBlocked]} />
          <View style={styles.statusTextWrap}>
            <Text style={liveReadiness?.live_enabled ? styles.connectedTitle : styles.blockedTitle}>
              {liveReadiness?.live_enabled ? `${selectedBrokerName} live ready` : `${selectedBrokerName} live locked`}
            </Text>
            <Text style={styles.cardText}>
              {liveReadiness?.backend_outbound_ip ? `Backend IP: ${liveReadiness.backend_outbound_ip}` : 'Backend IP: checking'}
            </Text>
          </View>
        </View>

        <Text style={styles.inputLabel}>Approved Static IP</Text>
        <TextInput
          style={styles.input}
          value={staticIp}
          onChangeText={setStaticIp}
          placeholder="Approved public IPv4"
          placeholderTextColor="#64748b"
          autoCapitalize="none"
          keyboardType="numbers-and-punctuation"
        />

        <View style={styles.switchRow}>
          <View style={styles.switchTextWrap}>
            <Text style={styles.cardTitle}>Registered with broker/exchange</Text>
            <Text style={styles.cardText}>Required before live orders are unlocked</Text>
          </View>
          <Switch
            value={staticIpRegistered}
            onValueChange={setStaticIpRegistered}
            trackColor={{ false: '#334155', true: '#065f46' }}
            thumbColor={staticIpRegistered ? '#10b981' : '#94a3b8'}
          />
        </View>

        {liveReadiness?.blockers?.length ? (
          <View style={styles.blockerBox}>
            {liveReadiness.blockers.map((item, index) => (
              <Text key={`${item}-${index}`} style={styles.blockerText}>{item}</Text>
            ))}
          </View>
        ) : null}

        <View style={styles.buttonRow}>
          <TouchableOpacity
            style={[styles.primaryButton, styles.buttonFlex, savingStaticIp && styles.buttonDisabled]}
            onPress={saveStaticIp}
            disabled={savingStaticIp}
          >
            <Text style={styles.primaryButtonText}>{savingStaticIp ? 'SAVING' : 'SAVE IP'}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.outlineButton, styles.buttonFlex]}
            onPress={() => fetchLiveReadiness(selectedBrokerId, true)}
          >
            <Text style={styles.outlineButtonText}>REFRESH IP</Text>
          </TouchableOpacity>
        </View>
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
      <TouchableOpacity style={styles.dangerButton} onPress={onLogout || (() => supabase.auth.signOut())}>
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
  content: { padding: 16, paddingBottom: 32, width: '100%', maxWidth: 960, alignSelf: 'center' },
  contentLandscape: { paddingVertical: 10 },
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
  brokerGrid: {
    gap: 8,
    marginBottom: 8,
  },
  brokerCard: {
    backgroundColor: '#101722',
    borderColor: '#273244',
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
  },
  brokerCardSelected: {
    borderColor: '#3b82f6',
    backgroundColor: '#0f1b2d',
  },
  statusPill: {
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 5,
    minWidth: 54,
    alignItems: 'center',
  },
  statusPillConnected: {
    backgroundColor: '#064e3b',
  },
  statusPillMuted: {
    backgroundColor: '#334155',
  },
  statusPillText: {
    color: '#f8fafc',
    fontSize: 9,
    fontWeight: '700',
  },
  cardTitle: { color: '#f8fafc', fontSize: 15, fontWeight: '700', marginBottom: 4 },
  cardText: { color: '#94a3b8', fontSize: 12, lineHeight: 17 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  rowText: { flex: 1, paddingRight: 12 },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
  statusDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: '#10b981', marginRight: 10 },
  statusDotLive: { backgroundColor: '#10b981' },
  statusDotBlocked: { backgroundColor: '#f59e0b' },
  statusTextWrap: { flex: 1 },
  connectedTitle: { color: '#10b981', fontSize: 14, fontWeight: '700', marginBottom: 3 },
  blockedTitle: { color: '#fbbf24', fontSize: 14, fontWeight: '700', marginBottom: 3 },
  inputLabel: { color: '#cbd5e1', fontSize: 11, fontWeight: '700', marginBottom: 7 },
  input: {
    backgroundColor: '#0b111c',
    borderColor: '#273244',
    borderWidth: 1,
    borderRadius: 6,
    color: '#f8fafc',
    fontSize: 14,
    minHeight: 42,
    paddingHorizontal: 12,
    marginBottom: 12,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 12,
  },
  switchTextWrap: { flex: 1 },
  blockerBox: {
    backgroundColor: '#1c1917',
    borderColor: '#78350f',
    borderWidth: 1,
    borderRadius: 6,
    padding: 10,
    marginBottom: 12,
  },
  blockerText: {
    color: '#fbbf24',
    fontSize: 11,
    lineHeight: 16,
    marginBottom: 3,
  },
  buttonRow: {
    flexDirection: 'row',
    gap: 10,
  },
  buttonFlex: {
    flex: 1,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
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

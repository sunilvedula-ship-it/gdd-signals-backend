import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, ScrollView, TextInput, TouchableOpacity, ActivityIndicator, Linking, Alert } from 'react-native';
import { BACKEND_URL } from '../config';
import { supabase } from '../supabase';


export default function SettingsScreen({ session, onPurge }) {
  const [brokers, setBrokers] = useState([]);
  const [clientId, setClientId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [loading, setLoading] = useState(true);

  const handleLogout = async () => {
    try {
      await supabase.auth.signOut();
    } catch (error) {
      console.error("Error signing out:", error);
    }
  };

  const handlePurgeData = () => {
    Alert.alert(
      "Confirm Database Purge",
      "This will permanently delete all signals and positions from the database. This action is irreversible. Proceed?",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Purge Data", style: "destructive", onPress: executePurge }
      ]
    );
  };

  const executePurge = async () => {
    setLoading(true);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      const response = await fetch(`${BACKEND_URL}/api/admin/purge-test-data`, {
        method: 'POST',
        headers
      });
      const data = await response.json();
      if (response.ok) {
        if (onPurge) {
          onPurge();
        }
        Alert.alert("Purge Successful", `Database cleaned successfully.\n- Signals Deleted: ${data.purged_signals}\n- Positions Deleted: ${data.purged_positions}`);
      } else {
        Alert.alert("Purge Failed", data.detail || "Could not clear data.");
      }
    } catch (error) {
      Alert.alert("Network Error", error.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchCredentials = async () => {
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      const response = await fetch(`${BACKEND_URL}/api/credentials`, { headers });
      const data = await response.json();
      setBrokers(data.brokers);
    } catch (error) {
      console.error("Error loading credentials:", error);
    } finally {
      setLoading(false);
    }
  };

  const saveCredentials = async () => {
    if (!clientId || !apiKey || !apiSecret) {
      alert("Please fill in Client ID, API Key and Secret.");
      return;
    }
    setLoading(true);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      await fetch(`${BACKEND_URL}/api/credentials`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          broker_id: 'flattrade',
          api_key: apiKey,
          api_secret: apiSecret,
          extra: {
            client_id: clientId
          }
        })
      });
      setClientId('');
      setApiKey('');
      setApiSecret('');
      fetchCredentials();
    } catch (error) {
      console.error("Error saving credentials:", error);
    } finally {
      setLoading(false);
    }
  };

  const deleteCredentials = async (brokerId) => {
    setLoading(true);
    try {
      const headers = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      await fetch(`${BACKEND_URL}/api/credentials/${brokerId}`, {
        method: 'DELETE',
        headers
      });
      fetchCredentials();
    } catch (error) {
      console.error("Error deleting credentials:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentials();
  }, []);

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  const configuredBroker = brokers.find(b => b.configured);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>1. Broker Account Setup</Text>
      <View style={styles.card}>
        <Text style={styles.cardText}>Don't have a zero-brokerage account? Open an account with Flattrade (our default execution broker) to get started.</Text>
        <TouchableOpacity style={styles.linkBtn} onPress={() => Linking.openURL('https://flattrade.in/')}>
          <Text style={styles.linkBtnText}>Open Flattrade Account ↗</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.sectionTitle}>2. Link Broker API Keys</Text>
      <View style={styles.card}>
        {configuredBroker ? (
          <View style={styles.configuredContainer}>
            <Text style={styles.configuredTitle}>✓ Configured: {configuredBroker.name}</Text>
            {configuredBroker.info?.extra_fields?.includes('client_id') && (
              <Text style={styles.configuredSub}>Linked Client ID: {configuredBroker.info?.api_key_masked ? 'Yes' : 'No'}</Text>
            )}
            <Text style={styles.configuredSub}>API Key: {configuredBroker.info?.api_key_masked || 'Configured'}</Text>
            
            <TouchableOpacity 
              style={styles.authorizeBtn} 
              onPress={() => Linking.openURL(`${BACKEND_URL}/api/broker/login/${configuredBroker.id}?token=${session?.access_token || ''}`)}
            >
              <Text style={styles.authorizeBtnText}>⚡ Login & Authorize Live Session</Text>
            </TouchableOpacity>
            
            <TouchableOpacity style={styles.deleteBtn} onPress={() => deleteCredentials(configuredBroker.id)}>
              <Text style={styles.deleteBtnText}>Delete API Credentials</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View>
            <Text style={styles.label}>Select Broker</Text>
            <View style={styles.pickerPlaceholder}>
              <Text style={styles.pickerText}>Flattrade (Default)</Text>
            </View>
            
            <TextInput 
              style={styles.input} 
              placeholder="Client ID (e.g. FCCOM623)" 
              placeholderTextColor="#6b7280"
              value={clientId}
              onChangeText={setClientId}
            />
            <TextInput 
              style={styles.input} 
              placeholder="API Key" 
              placeholderTextColor="#6b7280"
              value={apiKey}
              onChangeText={setApiKey}
            />
            <TextInput 
              style={styles.input} 
              placeholder="API Secret" 
              placeholderTextColor="#6b7280"
              secureTextEntry
              value={apiSecret}
              onChangeText={setApiSecret}
            />
            <TouchableOpacity style={styles.saveBtn} onPress={saveCredentials}>
              <Text style={styles.saveBtnText}>Save API Credentials</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      <Text style={styles.sectionTitle}>3. Subscribe to Tradetron Strategies</Text>
      <View style={styles.card}>
        <Text style={styles.cardText}>Subscribe to our official automated algorithms to deploy them to your broker account:</Text>
        
        <TouchableOpacity style={styles.stratItem} onPress={() => Linking.openURL('https://tradetron.tech/strategy/5985482')}>
          <Text style={styles.stratText}>JGD BNF Option Buying</Text>
          <Text style={styles.stratArrow}>→</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.stratItem} onPress={() => Linking.openURL('https://tradetron.tech/strategy/8049757')}>
          <Text style={styles.stratText}>Sensex Option Buying</Text>
          <Text style={styles.stratArrow}>→</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.stratItem} onPress={() => Linking.openURL('https://tradetron.tech/strategy/2547331')}>
          <Text style={styles.stratText}>Banknifty Futures Positional</Text>
          <Text style={styles.stratArrow}>→</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.stratItem} onPress={() => Linking.openURL('https://tradetron.tech/strategy/2769106')}>
          <Text style={styles.stratText}>Nifty Futures Positional</Text>
          <Text style={styles.stratArrow}>→</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.sectionTitle}>4. Account Session</Text>
      <View style={styles.card}>
        <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
          <Text style={styles.logoutBtnText}>Log Out of Account</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.sectionTitle}>5. Developer Testing Controls</Text>
      <View style={styles.card}>
        <Text style={styles.cardText}>Wipe all database positions and signals to start with a completely fresh slate during your testing period.</Text>
        <TouchableOpacity style={styles.purgeBtn} onPress={handlePurgeData}>
          <Text style={styles.purgeBtnText}>Purge All Signals & Positions</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}


const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0a0e17',
  },
  content: {
    padding: 16,
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0a0e17',
    justifyContent: 'center',
    alignItems: 'center',
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 'bold',
    color: '#9ca3af',
    textTransform: 'uppercase',
    marginBottom: 8,
    marginTop: 12,
  },
  card: {
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  cardText: {
    fontSize: 12,
    color: '#9ca3af',
    lineHeight: 16,
    marginBottom: 12,
  },
  linkBtn: {
    backgroundColor: 'rgba(59, 130, 246, 0.15)',
    borderColor: 'rgba(59, 130, 246, 0.3)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    alignItems: 'center',
  },
  linkBtnText: {
    color: '#3b82f6',
    fontSize: 12,
    fontWeight: 'bold',
  },
  label: {
    fontSize: 11,
    color: '#9ca3af',
    marginBottom: 6,
  },
  pickerPlaceholder: {
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
    borderColor: 'rgba(255, 255, 255, 0.05)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
  },
  pickerText: {
    color: '#ffffff',
    fontSize: 13,
  },
  input: {
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
    borderColor: 'rgba(255, 255, 255, 0.05)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    color: '#ffffff',
    fontSize: 13,
    marginBottom: 10,
  },
  saveBtn: {
    backgroundColor: '#3b82f6',
    borderRadius: 8,
    padding: 12,
    alignItems: 'center',
  },
  saveBtnText: {
    color: '#ffffff',
    fontSize: 13,
    fontWeight: 'bold',
  },
  configuredContainer: {
    alignItems: 'center',
    paddingVertical: 10,
  },
  configuredTitle: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#10b981',
    marginBottom: 4,
  },
  configuredSub: {
    fontSize: 11,
    color: '#9ca3af',
    marginBottom: 16,
  },
  authorizeBtn: {
    backgroundColor: 'rgba(16, 185, 129, 0.12)',
    borderColor: 'rgba(16, 185, 129, 0.3)',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    alignItems: 'center',
    marginBottom: 12,
  },
  authorizeBtnText: {
    color: '#10b981',
    fontSize: 12,
    fontWeight: 'bold',
  },
  deleteBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    borderColor: 'rgba(239, 68, 68, 0.3)',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  deleteBtnText: {
    color: '#ef4444',
    fontSize: 11,
    fontWeight: 'bold',
  },
  stratItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.15)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.04)',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  stratText: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: '500',
  },
  stratArrow: {
    color: '#3b82f6',
    fontSize: 14,
    fontWeight: 'bold',
  },
  logoutBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.08)',
    borderColor: 'rgba(239, 68, 68, 0.25)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    alignItems: 'center',
  },
  logoutBtnText: {
    color: '#ef4444',
    fontSize: 12,
    fontWeight: 'bold',
  },
  purgeBtn: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    borderColor: 'rgba(239, 68, 68, 0.35)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    alignItems: 'center',
  },
  purgeBtnText: {
    color: '#ef4444',
    fontSize: 12,
    fontWeight: 'bold',
  },
});

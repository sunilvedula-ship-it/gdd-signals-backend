import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, ScrollView, TextInput, TouchableOpacity, ActivityIndicator, Linking } from 'react-native';
import { BACKEND_URL } from '../config';


export default function SettingsScreen() {
  const [brokers, setBrokers] = useState([]);
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchCredentials = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/credentials`);
      const data = await response.json();
      setBrokers(data.brokers);
    } catch (error) {
      console.error("Error loading credentials:", error);
    } finally {
      setLoading(false);
    }
  };

  const saveCredentials = async () => {
    if (!apiKey || !apiSecret) {
      alert("Please fill in both API Key and Secret.");
      return;
    }
    setLoading(true);
    try {
      await fetch(`${BACKEND_URL}/api/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          broker_id: 'flattrade',
          api_key: apiKey,
          api_secret: apiSecret,
          extra: {}
        })
      });
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
      await fetch(`${BACKEND_URL}/api/credentials/${brokerId}`, { method: 'DELETE' });
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
            <Text style={styles.configuredSub}>{configuredBroker.info.api_key_masked}</Text>
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
    align-items: 'center',
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
    align-items: 'center',
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
});

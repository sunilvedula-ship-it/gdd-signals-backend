import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, ScrollView, TouchableOpacity, ActivityIndicator } from 'react-native';
import { BACKEND_URL } from '../config';


export default function ConsentScreen() {
  const [consentSigned, setConsentSigned] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchConsent = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/consent`);
      const data = await response.json();
      setConsentSigned(data.consent_signed);
    } catch (error) {
      console.error("Error loading consent:", error);
    } finally {
      setLoading(false);
    }
  };

  const signConsent = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/consent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agreement_version: "v1.0" })
      });
      const data = await response.json();
      if (data.status === 'success') {
        setConsentSigned(true);
      }
    } catch (error) {
      console.error("Error signing consent:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConsent();
  }, []);

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.consentCard}>
        <View style={styles.iconContainer}>
          <Text style={styles.icon}>🛡️</Text>
        </View>
        <Text style={styles.title}>Daily Compliance Consent</Text>
        <Text style={styles.date}>Date: {new Date().toISOString().split('T')[0]}</Text>

        <ScrollView style={styles.scrollBox}>
          <Text style={styles.scrollTitle}>Terms & Risk Disclosure</Text>
          <Text style={styles.scrollText}>
            I hereby authorize the auto-execution of alerts on my broker account for today. I fully acknowledge that:{"\n\n"}
            1. Financial markets carry significant risk of capital loss.{"\n"}
            2. Past performance is not indicative of future results.{"\n"}
            3. The signals provided are directional index recommendations and final strategy selections are deployed at my own discretion.{"\n"}
            4. I am solely responsible for all profit and loss generated on my linked broker account.
          </Text>
        </ScrollView>

        <View style={[styles.statusBox, consentSigned ? styles.statusSuccess : styles.statusWarning]}>
          <Text style={[styles.statusText, consentSigned ? styles.textSuccess : styles.textWarning]}>
            {consentSigned ? '✓ Daily Consent Active' : '⚠ Action Required: Auto-Trade Paused'}
          </Text>
        </View>

        <TouchableOpacity 
          style={[styles.btn, consentSigned ? styles.btnDisabled : styles.btnActive]} 
          onPress={signConsent}
          disabled={consentSigned}
        >
          <Text style={styles.btnText}>
            {consentSigned ? 'Signed for Today' : 'I Acknowledge and Consent'}
          </Text>
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
    justifyContent: 'center',
    minHeight: '90%',
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0a0e17',
    justifyContent: 'center',
    align-items: 'center',
  },
  consentCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
  },
  iconContainer: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    justifyContent: 'center',
    align-items: 'center',
    marginBottom: 16,
  },
  icon: {
    fontSize: 24,
  },
  title: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 4,
  },
  date: {
    fontSize: 12,
    color: '#9ca3af',
    marginBottom: 16,
  },
  scrollBox: {
    height: 140,
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
    borderColor: 'rgba(255, 255, 255, 0.05)',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    width: '100%',
    marginBottom: 16,
  },
  scrollTitle: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 6,
  },
  scrollText: {
    fontSize: 11,
    color: '#9ca3af',
    lineHeight: 16,
  },
  statusBox: {
    width: '100%',
    padding: 12,
    borderRadius: 8,
    marginBottom: 16,
    alignItems: 'center',
  },
  statusWarning: {
    backgroundColor: 'rgba(245, 158, 11, 0.1)',
    borderColor: 'rgba(245, 158, 11, 0.2)',
    borderWidth: 1,
  },
  statusSuccess: {
    backgroundColor: 'rgba(16, 185, 129, 0.1)',
    borderColor: 'rgba(16, 185, 129, 0.2)',
    borderWidth: 1,
  },
  statusText: {
    fontSize: 12,
    fontWeight: 'bold',
  },
  textWarning: { color: '#f59e0b' },
  textSuccess: { color: '#10b981' },
  btn: {
    width: '100%',
    padding: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  btnActive: {
    backgroundColor: '#10b981',
  },
  btnDisabled: {
    backgroundColor: 'rgba(16, 185, 129, 0.3)',
  },
  btnText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: 'bold',
  },
});

import React, { useState } from 'react';
import { StyleSheet, View, Text, TextInput, TouchableOpacity, Alert, ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, Image, useWindowDimensions } from 'react-native';
import { BACKEND_URL } from '../config';
import { supabase } from '../supabase';

const TEST_LOGIN_PHONE = '919043055445';
const TEST_LOGIN_PASSWORD = '123456';

const cleanPhone = (value) => value.trim().replace(/[\s+-]/g, '');

export default function LoginScreen({ onTestLogin }) {
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const { width, height } = useWindowDimensions();
  const useWideLayout = width >= 720 && width > height;

  // Send OTP SMS
  const handleSendOTP = async () => {
    // Basic validation
    if (!phone || phone.length < 10) {
      Alert.alert("Invalid Phone", "Please enter a valid phone number with country code (e.g., 918919859974). Do not include '+' prefix.");
      return;
    }
    
    // Clean phone number (strip whitespace or dashes, make sure it starts with country code)
    let cleanedPhone = cleanPhone(phone);
    if (cleanedPhone.length === 10) cleanedPhone = `91${cleanedPhone}`;
    if (cleanedPhone === TEST_LOGIN_PHONE) {
      setOtpSent(true);
      Alert.alert("Testing Login", "Use 123456 as the 6-digit testing password.");
      return;
    }
    
    setLoading(true);
    // Send OTP request to Supabase Auth
    const { error } = await supabase.auth.signInWithOtp({ 
      phone: `+${cleanedPhone}` 
    });
    setLoading(false);
    
    if (error) {
      Alert.alert("SMS Error", error.message);
    } else {
      setOtpSent(true);
      Alert.alert("OTP Sent", "A 6-digit verification code has been sent to your phone number.");
    }
  };

  // Verify Code
  const handleVerifyOTP = async () => {
    if (!otp || otp.length !== 6) {
      Alert.alert("Error", "Please enter the 6-digit verification code.");
      return;
    }
    
    let cleanedPhone = cleanPhone(phone);
    if (cleanedPhone.length === 10) cleanedPhone = `91${cleanedPhone}`;

    if (cleanedPhone === TEST_LOGIN_PHONE && otp.trim() !== TEST_LOGIN_PASSWORD) {
      Alert.alert("Invalid Password", "Use 123456 for the testing number.");
      return;
    }

    if (cleanedPhone === TEST_LOGIN_PHONE) {
      setLoading(true);
      try {
        const response = await fetch(`${BACKEND_URL}/api/auth/test-login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone: `+${cleanedPhone}`, password: otp.trim() }),
        });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); } catch { data = null; }
        if (!response.ok || !data?.access_token) {
          Alert.alert("Testing Login Failed", data?.detail || "Could not sign in with the testing password.");
          return;
        }
        onTestLogin({
          access_token: data.access_token,
          token_type: data.token_type || 'bearer',
          user: data.user,
          is_test_session: true,
        });
      } catch (error) {
        Alert.alert("Network error", error.message);
      } finally {
        setLoading(false);
      }
      return;
    }

    setLoading(true);
    const { error } = await supabase.auth.verifyOtp({
      phone: `+${cleanedPhone}`,
      token: otp.trim(),
      type: 'sms',
    });
    setLoading(false);

    if (error) {
      Alert.alert("Verification Failed", error.message);
    }
  };

  return (
    <KeyboardAvoidingView 
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'} 
      style={styles.container}
    >
      <ScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={[styles.scrollContent, useWideLayout && styles.scrollContentWide]}
      >
        <View style={[styles.layout, useWideLayout && styles.layoutWide]}>
        <View style={[styles.header, useWideLayout && styles.headerWide]}>
          <Image 
            source={require('../assets/logo.png')} 
            style={[styles.logo, useWideLayout && styles.logoWide]}
            resizeMode="contain" 
          />
          <Text style={styles.title}>SKI Analytics</Text>
          <Text style={styles.subtitle}>Automated Trading & Real-Time Signals</Text>
        </View>

        <View style={[styles.card, useWideLayout && styles.cardWide]}>
          {!otpSent ? (
            <View>
              <Text style={styles.label}>Log in with Mobile Number</Text>
              <TextInput 
                style={styles.input} 
                placeholder="Phone (e.g., 918919859974)"
                placeholderTextColor="#6b7280"
                keyboardType="phone-pad"
                value={phone}
                onChangeText={setPhone}
                autoFocus
              />
              <Text style={styles.infoText}>
                We will send you a 6-digit verification code via SMS. International rates may apply.
              </Text>
              
              <TouchableOpacity 
                style={[styles.btn, loading && styles.btnDisabled]} 
                onPress={handleSendOTP}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color="#ffffff" />
                ) : (
                  <Text style={styles.btnText}>Send Verification Code</Text>
                )}
              </TouchableOpacity>
            </View>
          ) : (
            <View>
              <Text style={styles.label}>Enter 6-Digit Code</Text>
              <TextInput 
                style={styles.input} 
                placeholder="0 0 0 0 0 0"
                placeholderTextColor="#6b7280"
                keyboardType="number-pad"
                maxLength={6}
                value={otp}
                onChangeText={setOtp}
                textAlign="center"
                secureTextEntry={false}
                autoFocus
              />
              <Text style={styles.infoText}>
                Code sent to +{cleanPhone(phone)}. Please enter it above.
              </Text>
              
              <TouchableOpacity 
                style={[styles.btn, loading && styles.btnDisabled]} 
                onPress={handleVerifyOTP}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color="#ffffff" />
                ) : (
                  <Text style={styles.btnText}>Verify & Log In</Text>
                )}
              </TouchableOpacity>

              <TouchableOpacity 
                onPress={() => setOtpSent(false)} 
                style={styles.backBtn}
                disabled={loading}
              >
                <Text style={styles.backBtnText}>← Change Phone Number</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0a0e17',
  },
  scrollContent: {
    flexGrow: 1,
    padding: 24,
    justifyContent: 'center',
  },
  scrollContentWide: {
    paddingVertical: 16,
    paddingHorizontal: 40,
  },
  layout: {
    width: '100%',
    maxWidth: 520,
    alignSelf: 'center',
  },
  layoutWide: {
    maxWidth: 980,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  header: {
    alignItems: 'center',
    marginBottom: 32,
  },
  headerWide: {
    flex: 1,
    marginBottom: 0,
    paddingRight: 32,
  },
  logo: {
    width: 150,
    height: 150,
    marginBottom: 16,
  },
  logoWide: {
    width: 108,
    height: 108,
    marginBottom: 10,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 4,
    fontFamily: 'System',
  },
  subtitle: {
    fontSize: 14,
    color: '#9ca3af',
    textAlign: 'center',
    fontFamily: 'System',
  },
  card: {
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
    borderRadius: 16,
    padding: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 8,
  },
  cardWide: {
    flex: 1.15,
    maxWidth: 520,
  },
  label: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 12,
    fontFamily: 'System',
  },
  input: {
    backgroundColor: 'rgba(0, 0, 0, 0.25)',
    borderColor: 'rgba(255, 255, 255, 0.06)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 16,
    color: '#ffffff',
    fontSize: 16,
    marginBottom: 16,
    fontFamily: 'System',
  },
  infoText: {
    fontSize: 12,
    color: '#6b7280',
    lineHeight: 16,
    marginBottom: 20,
    fontFamily: 'System',
  },
  btn: {
    backgroundColor: '#3b82f6',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnDisabled: {
    backgroundColor: 'rgba(59, 130, 246, 0.5)',
  },
  btnText: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: 'bold',
    fontFamily: 'System',
  },
  backBtn: {
    marginTop: 16,
    alignItems: 'center',
  },
  backBtnText: {
    color: '#3b82f6',
    fontSize: 13,
    fontFamily: 'System',
  },
});

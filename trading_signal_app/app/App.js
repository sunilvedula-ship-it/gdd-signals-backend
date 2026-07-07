import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, StatusBar, ActivityIndicator, Platform, useWindowDimensions } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { supabase } from './supabase';

// Import Screens
import FeedScreen from './screens/FeedScreen';
import PaperTradeScreen from './screens/PaperTradeScreen';
import ConsentScreen from './screens/ConsentScreen';
import SettingsScreen from './screens/SettingsScreen';
import LoginScreen from './screens/LoginScreen';

// Custom icons mock
const TabIcon = ({ name, color, size }) => {
  let emoji = '📰';
  if (name === 'feed') emoji = '📰';
  else if (name === 'paper') emoji = '💼';
  else if (name === 'consent') emoji = '✍️';
  else if (name === 'settings') emoji = '⚙️';
  return <Text style={{ fontSize: size - 4, color }}>{emoji}</Text>;
};

const Tab = createBottomTabNavigator();
const TEST_SESSION_KEY = 'gdd_test_login_session';

export default function App() {
  const [session, setSession] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [purgeTrigger, setPurgeTrigger] = useState(0);
  const { width, height } = useWindowDimensions();
  const isLandscape = width > height;
  const isTablet = width >= 768;

  useEffect(() => {
    // 1. Get initial session
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session) {
        await AsyncStorage.removeItem(TEST_SESSION_KEY);
        setSession(session);
      } else {
        const storedTestSession = await AsyncStorage.getItem(TEST_SESSION_KEY);
        setSession(storedTestSession ? JSON.parse(storedTestSession) : null);
      }
      setInitializing(false);
    });

    // 2. Listen for auth changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      if (session) {
        await AsyncStorage.removeItem(TEST_SESSION_KEY);
        setSession(session);
      } else {
        const storedTestSession = await AsyncStorage.getItem(TEST_SESSION_KEY);
        setSession(storedTestSession ? JSON.parse(storedTestSession) : null);
      }
      setInitializing(false);
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  const handleTestLogin = async (testSession) => {
    await AsyncStorage.setItem(TEST_SESSION_KEY, JSON.stringify(testSession));
    setSession(testSession);
  };

  const handleLogout = async () => {
    await AsyncStorage.removeItem(TEST_SESSION_KEY);
    await supabase.auth.signOut();
    setSession(null);
  };

  if (initializing) {
    return (
      <View style={styles.loadingContainer}>
        <StatusBar barStyle="light-content" backgroundColor="#0a0e17" />
        <ActivityIndicator size="large" color="#3b82f6" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <StatusBar barStyle="light-content" backgroundColor="#0a0e17" />
        {!session ? (
          <SafeAreaView style={{ flex: 1, backgroundColor: '#0a0e17' }}>
            <LoginScreen onTestLogin={handleTestLogin} />
          </SafeAreaView>
        ) : (
          <Tab.Navigator
            screenOptions={({ route }) => ({
              headerStyle: {
                backgroundColor: '#111827',
                borderBottomWidth: 1,
                borderBottomColor: '#1f2937',
              },
              headerTintColor: '#ffffff',
              headerTitleStyle: {
                fontWeight: 'bold',
                fontFamily: 'System',
                fontSize: isLandscape ? 16 : 18,
              },
              tabBarStyle: {
                backgroundColor: '#111827',
                borderTopColor: '#1f2937',
                ...(Platform.OS === 'ios' ? {
                  height: isLandscape ? 58 : (isTablet ? 72 : 88),
                  paddingBottom: isLandscape ? 5 : (isTablet ? 14 : 28),
                  paddingTop: isLandscape ? 4 : 8,
                } : {}),
              },
              tabBarLabelStyle: {
                fontSize: isLandscape ? 10 : 11,
              },
              tabBarActiveTintColor: '#3b82f6',
              tabBarInactiveTintColor: '#9ca3af',
              tabBarIcon: ({ color, size }) => {
                let name;
                if (route.name === 'Signals') name = 'feed';
                else if (route.name === 'Paper Trade') name = 'paper';
                else if (route.name === 'Consent') name = 'consent';
                else if (route.name === 'Auto-Trade') name = 'settings';
                return <TabIcon name={name} color={color} size={size} />;
              },
            })}
          >
            <Tab.Screen name="Signals">
              {props => <FeedScreen {...props} session={session} purgeTrigger={purgeTrigger} />}
            </Tab.Screen>
            <Tab.Screen name="Paper Trade">
              {props => <PaperTradeScreen {...props} session={session} purgeTrigger={purgeTrigger} />}
            </Tab.Screen>
            <Tab.Screen name="Consent">
              {props => <ConsentScreen {...props} session={session} />}
            </Tab.Screen>
            <Tab.Screen name="Auto-Trade">
              {props => <SettingsScreen {...props} session={session} onLogout={handleLogout} onPurge={() => setPurgeTrigger(prev => prev + 1)} />}
            </Tab.Screen>
          </Tab.Navigator>
        )}
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0a0e17',
    justifyContent: 'center',
    alignItems: 'center',
  },
});

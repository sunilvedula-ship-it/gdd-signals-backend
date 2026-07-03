import React, { useState, useEffect } from 'react';
import { StyleSheet, View, Text, StatusBar, ActivityIndicator, Platform } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
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

export default function App() {
  const [session, setSession] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [purgeTrigger, setPurgeTrigger] = useState(0);

  useEffect(() => {
    // 1. Get initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setInitializing(false);
    });

    // 2. Listen for auth changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      setInitializing(false);
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

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
            <LoginScreen />
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
              },
              tabBarStyle: {
                backgroundColor: '#111827',
                borderTopColor: '#1f2937',
                ...(Platform.OS === 'ios' ? {
                  height: 88,
                  paddingBottom: 28,
                  paddingTop: 8,
                } : {}),
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
              {props => <SettingsScreen {...props} session={session} onPurge={() => setPurgeTrigger(prev => prev + 1)} />}
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

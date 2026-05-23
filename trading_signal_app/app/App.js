import React from 'react';
import { StyleSheet, View, Text, StatusBar } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

// Import Screens (or defined in-file for clean single-entry structure)
import FeedScreen from './screens/FeedScreen';
import PaperTradeScreen from './screens/PaperTradeScreen';
import ConsentScreen from './screens/ConsentScreen';
import SettingsScreen from './screens/SettingsScreen';

// Custom icons mock (since vector icons depend on expo configuration)
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
  return (
    <NavigationContainer>
      <StatusBar barStyle="light-content" backgroundColor="#0a0e17" />
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
            height: 60,
            paddingBottom: 8,
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
        <Tab.Screen name="Signals" component={FeedScreen} />
        <Tab.Screen name="Paper Trade" component={PaperTradeScreen} />
        <Tab.Screen name="Consent" component={ConsentScreen} />
        <Tab.Screen name="Auto-Trade" component={SettingsScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}

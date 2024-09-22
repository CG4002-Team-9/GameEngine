#!/usr/bin/env python

import asyncio
import json
import os
from dotenv import load_dotenv
import aio_pika
import aiomqtt
import purge_queues

# Load environment variables from .env file
load_dotenv()

# Use the same broker, username, and password for both RabbitMQ and MQTT
BROKER = os.getenv('BROKER')
BROKERUSER = os.getenv('BROKERUSER')
PASSWORD = os.getenv('PASSWORD')

# Abstracted RabbitMQ port
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', '5672'))

# MQTT broker settings (same as RabbitMQ)
MQTT_BROKER = BROKER
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))

# RabbitMQ queues
UPDATE_EVAL_SERVER_QUEUE = os.getenv('UPDATE_EVAL_SERVER_QUEUE', 'update_eval_server_queue')
UPDATE_GE_QUEUE = os.getenv('UPDATE_GE_QUEUE', 'update_ge_queue') 

# MQTT topic
MQTT_TOPIC_UPDATE_EVERYONE = os.getenv('MQTT_TOPIC_UPDATE_EVERYONE', 'update_everyone')

# Example full schema for messages to and from the game engine
"""
Schema for messages received from 'update_ge_queue' (from various sources):

{
  "update": true,                 # Indicates whether to send an update to all nodes
  "action": false,                # When true, perform calculations and update the game state
  "player_id": 1,                 # ID of the player performing the action
  "action_type": "gun",           # Type of action performed
  "hit": true,                    # For 'gun' action, indicates if the shot hit the target
  "game_state": {
    "p1": {
      "opponent_visible": false,
      "opponent_in_rain_bomb": 0,  # Counter for rain bombs affecting the player
      "disconnected": false,
      "login": true
    },
    "p2": {
      "opponent_visible": false,
      "opponent_in_rain_bomb": 0,
      "disconnected": false,
      "login": true
    }
  }
}

Schema for messages published to 'update_everyone' (to Nodes):

{
  "player_id": 1,
  "action": "gun",
  "game_state": { ... }  # Updated game state after calculations
}

Schema for messages published to 'update_eval_server_queue' (to Evaluation Server):

{
  "player_id": 1,
  "action": "gun",
  "game_state": { ... }  # Updated game state after calculations
}
"""

class GameEngine:
    def __init__(self):
        self.rabbitmq_connection = None
        self.channel = None
        self.update_ge_queue = None
        self.mqtt_client = None
        # Initialize internal game state
        self.game_state = {
            'p1': {
                'hp': 100,
                'bullets': 6,
                'bombs': 2,
                'shield_hp': 0,
                'deaths': 0,
                'shields': 3,
                'opponent_visible': False,
                'opponent_in_rain_bomb': 0,  # Counter for rain bombs
                'disconnected': False,
                'login': False
            },
            'p2': {
                'hp': 100,
                'bullets': 6,
                'bombs': 2,
                'shield_hp': 0,
                'deaths': 0,
                'shields': 3,
                'opponent_visible': False,
                'opponent_in_rain_bomb': 0,
                'disconnected': False,
                'login': False
            }
        }

    async def setup_rabbitmq(self):
        # Set up RabbitMQ connection using aio_pika
        print('[DEBUG] Connecting to RabbitMQ broker...')
        self.rabbitmq_connection = await aio_pika.connect_robust(
            host=BROKER,
            port=RABBITMQ_PORT,
            login=BROKERUSER,
            password=PASSWORD,
        )
        self.channel = await self.rabbitmq_connection.channel()
        # Declare the update_ge_queue
        self.update_ge_queue = await self.channel.declare_queue(UPDATE_GE_QUEUE, durable=True)
        print(f'[DEBUG] Connected to RabbitMQ broker at {BROKER}:{RABBITMQ_PORT}')

    async def publish_to_update_eval_server_queue(self, message):
        # Publish message to update_eval_server_queue
        message_body = json.dumps(message).encode('utf-8')
        await self.channel.default_exchange.publish(
            aio_pika.Message(body=message_body),
            routing_key=UPDATE_EVAL_SERVER_QUEUE,
        )
        print(f'[DEBUG] Published message to {UPDATE_EVAL_SERVER_QUEUE}: {json.dumps(message, indent = 2)}')

    def update_internal_game_state(self, incoming_game_state):
        # Update internal game state with the incoming data
        for player_key in ['p1', 'p2']:
            if player_key in incoming_game_state:
                self.game_state[player_key].update(incoming_game_state[player_key])

    def perform_action(self, player_id, action_type, data):
        # Perform calculations based on action_type
        player_key = f'p{player_id}'
        opponent_key = 'p1' if player_key == 'p2' else 'p2'

        player = self.game_state[player_key]
        opponent = self.game_state[opponent_key]

        # Extract necessary data
        opponent_visible = player.get('opponent_visible', False)
        opponent_in_rain_bomb = player.get('opponent_in_rain_bomb', 0)
        hit = data.get('hit', False)

        # Initialize damage variables
        damage_to_opponent = 0
        damage_to_shield = 0

        # Handle actions
        if action_type == 'gun':
            #When player has ammo
            if player['bullets'] > 0:
                player['bullets'] -= 1
                if hit:
                    damage_to_opponent = 5  # Gun shot results in -5 HP
                print(f'[DEBUG] Player {player_id} fired a gun. Bullets left: {player["bullets"]}. Hit: {hit}')
        elif action_type == 'bomb':
            if player['bombs'] > 0 and opponent_visible:
                # Reduce bombs
                player['bombs'] -= 1
                print(f'[DEBUG] Player {player_id} threw a bomb. Bombs left: {player["bombs"]}')
                # Inflict immediate damage
                damage_to_opponent = 5
        elif action_type == 'reload':
            # Can only reload if bullets are zero
            if player['bullets'] == 0:
                player['bullets'] = 6
                print(f'[DEBUG] Player {player_id} reloaded. Bullets: {player["bullets"]}')
        elif action_type == 'shield':
            if player['shields'] > 0 and player['shield_hp'] == 0:
                # Reduce shields count
                player['shields'] -= 1
                # Reset shield HP
                player['shield_hp'] = 30
                print(f'[DEBUG] Player {player_id} activated a shield. Shields left: {player["shields"]}')
        elif action_type == 'logout':
            player['login'] = False
        elif action_type in ['basket', 'volley', "soccer", "bowl"]:
            # AI actions inflict damage only if opponent is visible
            if opponent_visible:
                damage_to_opponent = 10  # All other attacks result in -10 HP
        # Handle rain damage
        if opponent_in_rain_bomb > 0 and opponent_visible:
            damage_to_opponent += 5 * opponent_in_rain_bomb  # -5 HP per rain bomb active

        # Apply damage to opponent's shield first
        if opponent['shield_hp'] > 0:
            if damage_to_opponent > 0:
                damage_to_shield = min(damage_to_opponent, opponent['shield_hp'])
                opponent['shield_hp'] -= damage_to_shield
                damage_to_opponent -= damage_to_shield
                print(f'[DEBUG] Damage to shield: {damage_to_shield}. Shield HP left: {opponent["shield_hp"]}')
        else:
            opponent['shield_hp'] = 0

        # Apply remaining damage to opponent's HP
        if damage_to_opponent > 0:
            opponent['hp'] -= damage_to_opponent
            print(f'[DEBUG] Damage to opponent HP: {damage_to_opponent}. HP left: {opponent["hp"]}')
            if opponent['hp'] <= 0:
                opponent['hp'] = 100  # Rebirth with full HP
                opponent['deaths'] += 1
                opponent['shields'] = 3
                opponent['shield_hp'] = 0
                opponent['bullets'] = 6
                opponent['bombs'] = 2
                print(f'[DEBUG] Player {opponent_key[-1]} died and respawned.')

    async def process_message(self, message: aio_pika.IncomingMessage):
        async with message.process():
            print('[DEBUG] Received message from RabbitMQ queue "update_ge_queue"')
            data = json.loads(message.body.decode('utf-8'))
            print(f'[DEBUG] Message content:\n{json.dumps(data, indent=2)}')

            action_performed = data.get("action", False)
            to_update = data.get("update", False)
            player_id = data.get('player_id')
            action_type = data.get('action_type')
            incoming_game_state = data.get('game_state', {})

            # Update internal game state with non-action-related info
            self.update_internal_game_state(incoming_game_state)

            if action_performed:
                # Perform action calculations before updating internal state
                self.perform_action(player_id, action_type, data)
                # Prepare message to publish
                mqtt_message = {
                    "game_state": self.game_state,
                    "action": action_type,
                    "player_id": player_id
                }
                mqtt_message_str = json.dumps(mqtt_message)
                # Publish to update_eval_server_queue
                await self.publish_to_update_eval_server_queue(mqtt_message)
                
                # Publish to MQTT topic
                await self.mqtt_client.publish(
                    MQTT_TOPIC_UPDATE_EVERYONE,
                    mqtt_message_str.encode('utf-8'),
                    qos=2
                )
                print(f'[DEBUG] Published message to MQTT topic {MQTT_TOPIC_UPDATE_EVERYONE}: {json.dumps(mqtt_message, indent = 2)}')
            elif to_update:
                # Prepare message to publish
                mqtt_message = {
                    "game_state": self.game_state
                }
                mqtt_message_str = json.dumps(mqtt_message)
                # Publish to MQTT topic
                await self.mqtt_client.publish(
                    MQTT_TOPIC_UPDATE_EVERYONE,
                    mqtt_message_str.encode('utf-8'),
                    qos=2
                )
                print(f'[DEBUG] Published message to MQTT topic {MQTT_TOPIC_UPDATE_EVERYONE}: {json.dumps(mqtt_message, indent = 2)}')
            else:
                # Only update internal game state without sending messages
                print(f'Game state updated internally: {json.dumps(self.game_state, indent=2)}')
                print('[DEBUG] Updated internal game state without sending any messages')

    async def run(self):
        # Create instance of QueuePurger and purge the queues before running the game engine
        purger = purge_queues.QueuePurger()
        print('[DEBUG] Purging queues before starting the game engine...')
        await purger.run_purge()  # Purge the queues
        
        # print the starting game state
        print(f'[DEBUG] Starting game state: {json.dumps(self.game_state, indent=2)}')
        
        await self.setup_rabbitmq()

        # Set up MQTT client using aiomqtt
        print('[DEBUG] Connecting to MQTT broker...')
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=BROKERUSER,
            password=PASSWORD
        ) as self.mqtt_client:
            print(f'[DEBUG] Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}')
            # Start consuming messages
            await self.update_ge_queue.consume(self.process_message)
            print('[DEBUG] Started consuming messages from update_ge_queue')
            # Keep the program running
            await asyncio.Future()

if __name__ == '__main__':
    game_engine = GameEngine()
    try:
        asyncio.run(game_engine.run())
    except KeyboardInterrupt:
        print('[DEBUG] Game engine stopped by user')
    except Exception as e:
        print(f'[ERROR] {e}')

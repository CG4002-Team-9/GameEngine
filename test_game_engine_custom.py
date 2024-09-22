#!/usr/bin/env python

import asyncio
import json
import os
from dotenv import load_dotenv
import aio_pika

# Load environment variables from .env file
load_dotenv()

# Broker configurations
BROKER = os.getenv('BROKER')
BROKERUSER = os.getenv('BROKERUSER')
PASSWORD = os.getenv('PASSWORD')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', '5672'))

# RabbitMQ queues
UPDATE_GE_QUEUE = 'update_ge_queue'

class GameEngineTest:
    def __init__(self):
        self.rabbitmq_connection = None
        self.channel = None

    async def setup_rabbitmq(self):
        # Connect to RabbitMQ
        self.rabbitmq_connection = await aio_pika.connect_robust(
            host=BROKER,
            port=RABBITMQ_PORT,
            login=BROKERUSER,
            password=PASSWORD,
        )
        self.channel = await self.rabbitmq_connection.channel()
        # Declare queues
        await self.channel.declare_queue(UPDATE_GE_QUEUE, durable=True)
        print('[DEBUG] Connected to RabbitMQ and declared queues')

    async def send_test_message(self, message):
        # Publish message to update_ge_queue
        message_body = json.dumps(message).encode('utf-8')
        await self.channel.default_exchange.publish(
            aio_pika.Message(body=message_body),
            routing_key=UPDATE_GE_QUEUE,
        )
        print(f'[DEBUG] Published test message to {UPDATE_GE_QUEUE}: {message}')

    async def run_test(self):
        await self.setup_rabbitmq()

        while True:
            # Get custom inputs from user
            player_id_input = input('Enter player ID (integer) or type "no" to exit: ')
            if player_id_input.lower() == 'no':
                print('Exiting...')
                break

            try:
                player_id = int(player_id_input)
            except ValueError:
                print("Invalid input. Please enter a valid integer or 'no' to exit.")
                continue

            # Provide available action types and get input
            action_type = input(
                'Enter action type (available: gun, soccer, basket, shield, volley, bomb, bowl, logout) or type "no" to exit: '
            ).lower()
            
            if action_type == 'no':
                print('Exiting...')
                break

            available_actions = ['gun', 'soccer', 'basket', 'shield', 'volley', 'bomb', 'bowl', 'logout', 'reload']
            if action_type not in available_actions:
                print(f"Invalid action type. Please choose from {', '.join(available_actions)} or 'no' to exit.")
                continue

            # Simulating player action detected from wearables / rain bomb damage tick in visualizer
            # test_message = {
            #     'action': True,
            #     'player_id': player_id,
            #     'action_type': action_type,
            #     'hit': True,
            #     'game_state': {
            #         'p1': {'opponent_visible': True,
            #                'opponent_in_rain_bomb': 2},
            #         'p2': {'opponent_visible': True,
            #                'opponent_in_rain_bomb': 0}
            #     }
            # }
            
            # Simulate Game Engine updates from eval server
            # test_message = {
            #     'update': True,
            #     'game_state': {
            #         'p1': {
            #             'hp': 90,
            #             'bullets': 6,
            #             'bombs': 2,
            #             'shield_hp': 0,
            #             'deaths': 0,
            #             'shields': 3,
            #         },
            #         'p2': {
            #             'hp': 100,
            #             'bullets': 6,
            #             'bombs': 2,
            #             'shield_hp': 0,
            #             'deaths': 0,
            #             'shields': 3,
            #         }
            #     }
            # }
            
            # Simulate visualizer communications with server
            # test_message = {
            #     'game_state': {
            #         'p1': {'opponent_visible': True,
            #                'opponent_in_rain_bomb': 2},
            #         'p2': {'opponent_visible': True,
            #                'opponent_in_rain_bomb': 2}
            #     }
            # }
            
            # Send the test message
            await self.send_test_message(test_message)

        # Close RabbitMQ connection after finishing
        await self.rabbitmq_connection.close()


if __name__ == '__main__':
    test = GameEngineTest()
    try:
        asyncio.run(test.run_test())
    except KeyboardInterrupt:
        print('Process interrupted. Exiting...')

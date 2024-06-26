import requests
import time
import random

def check_server_load(url, num_requests=100, interval=1):
    """
    Проверяет нагрузку сервера, отправляя запросы и измеряя время ответа.

    Args:
        url: Адрес сервера.
        num_requests: Количество запросов, которые нужно отправить.
        interval: Интервал между запросами в секундах.

    Returns:
        Список времени ответов сервера в секундах.
    """

    response_times = []
    for i in range(num_requests):
        start_time = time.time()
        response = requests.get(url)
        end_time = time.time()
        response_time = end_time - start_time
        response_times.append(response_time)
        time.sleep(interval + random.uniform(0, 0.5))  # Добавляем рандомную задержку

    return response_times

if __name__ == "__main__":
    server_url = input("Введите адрес сервера: ")
    num_requests = int(input("Введите количество запросов: "))
    interval = float(input("Введите интервал между запросами (в секундах): "))

    response_times = check_server_load(server_url, num_requests, interval)

    print("Время ответов сервера (в секундах):")
    for time in response_times:
        print(time)

    # Дополнительная обработка:
    average_response_time = sum(response_times) / len(response_times)
    print(f"Среднее время ответа: {average_response_time:.2f} секунд")
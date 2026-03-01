import axios from 'axios';
import dotenv from 'dotenv';

// Load environment variables
dotenv.config();

/**
 * Login to Angel One using client credentials and TOTP
 * @param {string} totpCode - The TOTP code from your authenticator app
 * @returns {Promise<Object>} - Response containing auth tokens
 */
export async function loginByPassword(totpCode) {
    const data = JSON.stringify({
        clientcode: process.env.ANGEL_CLIENT_ID,
        password: process.env.ANGEL_CLIENT_PIN,
        totp: totpCode,
        state: process.env.ANGEL_STATE || 'STATE'
    });

    const config = {
        method: 'post',
        url: 'https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-UserType': 'USER',
            'X-SourceID': 'WEB',
            'X-ClientLocalIP': process.env.ANGEL_CLIENT_LOCAL_IP,
            'X-ClientPublicIP': process.env.ANGEL_CLIENT_PUBLIC_IP,
            'X-MACAddress': process.env.ANGEL_MAC_ADDRESS,
            'X-PrivateKey': process.env.ANGEL_API_KEY
        },
        data: data
    };

    try {
        const response = await axios(config);
        console.log('Login successful:', JSON.stringify(response.data));
        return response.data;
    } catch (error) {
        console.error('Login failed:', error.message);
        throw error;
    }
}

// Run directly if this file is executed
// Usage: node src/services/angelOneAuth.js <TOTP_CODE>
if (process.argv[1].includes('angelOneAuth')) {
    const totpCode = process.argv[2];
    if (!totpCode) {
        console.error('Usage: node src/services/angelOneAuth.js <TOTP_CODE>');
        process.exit(1);
    }
    loginByPassword(totpCode)
        .then(data => console.log('Response:', data))
        .catch(err => console.error('Error:', err.message));
}

export default { loginByPassword };

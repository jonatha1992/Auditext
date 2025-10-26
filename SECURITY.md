# Security Policy

## 🔒 Reporting Security Issues

If you discover a security vulnerability in Auditext, please email [your-email@example.com] instead of using the public issue tracker.

We take all security reports seriously and will respond as quickly as possible.

## ✅ Security Measures Implemented

### Git History Sanitization
- ✅ **`.env` removed from git history** - All environment files have been completely removed from all branches
- ✅ **Hugging Face token removed** - All hardcoded API tokens have been eliminated from the repository history
- ✅ **Clean commit history** - No sensitive data remains in any commit across all branches

### Configuration Security
- ✅ **Environment variables** - All secrets managed via `.env` file (excluded from git)
- ✅ **SECRET_KEY validation** - Django SECRET_KEY loaded from environment with validation
- ✅ **DEBUG mode protection** - Automatic warnings for insecure configurations
- ✅ **Secure defaults** - Production-safe defaults for all critical settings

### Input Validation ⚠️ (Recommended)
*Note: These features are documented but not yet implemented. See SECURITY.md for implementation guide.*

- File type validation (whitelist audio extensions)
- File size limits (configurable via MAX_AUDIO_FILE_SIZE)
- Filename sanitization (path traversal prevention)
- Model name validation (whitelist Whisper models)

## 🚨 Important Security Notes

### 1. CSRF Protection
The `/api/transcribe/` endpoint currently uses `@csrf_exempt` for API compatibility. This is **acceptable for development** but should be addressed before production:

**Options for production:**
- Implement API key authentication
- Add rate limiting (django-ratelimit)
- Use JWT tokens
- Enable session-based authentication

### 2. Environment Configuration
Before deploying to production:

```bash
# Generate a strong SECRET_KEY
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'

# Update .env file
SECRET_KEY=<generated-key-here>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

### 3. Hugging Face Token (if needed)
If you need to use Hugging Face models with authentication:

1. Create a new token at: https://huggingface.co/settings/tokens
2. **NEVER** commit the token to git
3. Add to `.env` file: `HUGGINGFACE_TOKEN=your_token_here`
4. Access via: `os.getenv("HUGGINGFACE_TOKEN")`

**⚠️ A Hugging Face token was previously exposed in the git history and should be considered compromised. It has been removed from all commits.**

## 📋 Pre-Production Checklist

### Critical (Must Do)
- [ ] Set `DEBUG=False` in production `.env`
- [ ] Generate and set new `SECRET_KEY`
- [ ] Configure `ALLOWED_HOSTS` with your domain
- [ ] Enable HTTPS/SSL certificates
- [ ] Review and revoke any exposed tokens

### Recommended (Should Do)
- [ ] Implement file upload validation
- [ ] Add rate limiting to API endpoints
- [ ] Set up monitoring and logging
- [ ] Configure firewall rules
- [ ] Update all dependencies to latest versions
- [ ] Set up automated security scanning

### Optional (Nice to Have)
- [ ] Implement API authentication
- [ ] Add Content Security Policy headers
- [ ] Set up automated backups
- [ ] Configure CDN for static files
- [ ] Implement request logging

## 🔄 Security Update History

### 2025-10-26 - Initial Security Audit
- **Action**: Removed `.env` from git history across all branches
- **Action**: Removed hardcoded Hugging Face token from git history
- **Action**: Cleaned and force-pushed all branches to GitHub
- **Impact**: No sensitive data remains in repository history
- **Status**: ✅ Safe to make repository public

### Token Compromise
- **Token Type**: Hugging Face User Access Token
- **Token Value**: `hf_WsSo**********************` (redacted - first 7 chars shown)
- **Original Location**: Commit `f3dd69f`, file `apps/core/whisper_utils.py:46` (now removed)
- **Status**: Removed from all git history
- **Action Required**: If this was your token, revoke it at https://huggingface.co/settings/tokens
- **Remediation**: Always use environment variables (`os.getenv()`) for tokens

## 📚 Security Best Practices

1. **Never commit secrets**: Always use `.env` files (already in `.gitignore`)
2. **Rotate credentials**: Change SECRET_KEY and tokens periodically
3. **Monitor logs**: Review security events regularly
4. **Keep dependencies updated**: Run `pip list --outdated` monthly
5. **Use HTTPS**: Always use TLS/SSL in production
6. **Validate inputs**: Never trust user input
7. **Sanitize outputs**: Prevent XSS through proper escaping
8. **Limit file sizes**: Prevent DoS attacks
9. **Rate limit APIs**: Prevent abuse
10. **Use secure defaults**: Fail securely

## 🔗 Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Django Security Documentation](https://docs.djangoproject.com/en/stable/topics/security/)
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)

## 📞 Contact

For security concerns, please contact:
- Email: [your-email@example.com]
- GitHub Security Advisories: https://github.com/jonatha1992/Auditext/security/advisories

---

**Last Updated**: 2025-10-26
**Version**: 1.0
**Status**: Repository cleaned and safe for public release
